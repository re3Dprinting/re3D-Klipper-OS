/*
  DP_Motor_Logger.ino
  ----------------------------------------------------------------------------
  One Arduino Leonardo doing both jobs, combined from
    motor_torque_autostart.ino   RoboClaw 2x7A, model-based constant torque
    DPTA_4-20mA_Reader.ino       ProSense DPTA-20-10 -> NCD PR33-8 (ADS1115)
  and printing ONE combined CSV line per data period.

  Every line that is NOT data starts with '#', so the data lines are pure CSV.
  Run dp_motor_logger.py on the PC to get one .csv file per run automatically,
  or use the Serial Monitor (115200 baud, "Newline") and copy/paste.

  WIRING - unchanged from the two original sketches (they use different pins):
    RoboClaw : Leonardo pin 1 (TX) -> S1, pin 0 (RX) -> S2, GND -> GND   [Serial1]
               Motion Studio: Packet Serial, address 0x80, 115200 baud,
               "Swap Encoder Channels" UNTICKED, serial timeout ~0.5 s.
    PR33-8   : I2C IN GND -> GND, +5V -> 5V, SDA -> SDA (D2), SCL -> SCL (D3)
               jumpers: both "4.7K PULL" installed, A0 removed (address 0x48)
               The PR33-8 powers the loop (no external loop supply):
               IN1 -> DPTA "+",  DPTA "-" -> [DMM only while calibrating] -> G

  COMMANDS (type, then Enter)
    g label  GO: new run. Time resets to 0, CSV header printed, motor starts.
             The label is optional; the PC logger puts it in the file name.
    x        STOP: motor off and the run ends (the PC logger saves the .csv).
    1 / 0    motor on / off WITHOUT starting or ending a run
             (e.g. 0 then x a few seconds later to record the coast-down).
    i0.70    target motor current in A (0.00 - 0.90). "i" alone shows it.
    t250     data period in ms (50 - 5000)
    m        data stream on (reprints the CSV header)
    p        pause the data stream (the motor keeps doing what it was doing)
    z / s    DPTA zero / span trim display
    r        set the span reference pressure
    c        calibrate the receiver against a DMM (2 points, saved to EEPROM)
    n        reset the receiver calibration to nominal
    a        scan all four ADS1115 inputs
    h        help + status

  CSV COLUMNS
    t_s       seconds since the run started (since power-up before any run)
    motor_on  1 = torque control running, 0 = off
    rpm       output-shaft speed (still read while off, so coast-down shows)
    dp_inwc   differential pressure, inches of water column (blank = no reading)
    Problems (RoboClaw not answering, no pressure readings, RoboClaw status word)
    are reported as '#' lines, which the PC logger keeps out of the CSV.

  WHAT CHANGED vs. THE TWO ORIGINALS
    - The ADC is read without blocking (one conversion at a time in the
      background), so the 10 ms motor control loop never waits for it.
      The old reader blocked for ~270 ms per printed line.
    - The motor loop keeps running during the calibration prompts as well.
    - Motor does NOT start by itself at power-up (set AUTOSTART = true below for
      the old behaviour).
    - If the RoboClaw stops answering for ~0.6 s while running, the motor is
      switched off and stays off until you send 1 or g, so it can't come back on
      by surprise when RoboClaw power returns.
  ----------------------------------------------------------------------------
*/

#include <Wire.h>
#include <EEPROM.h>
#include <Basicmicro.h>   // Library Manager: search "Basicmicro"

// ============================ USER SETTINGS =================================
const bool          AUTOSTART      = false;  // true = motor starts at power-up as soon
                                             // as the RoboClaw answers (no run/file)
const float         TARGET_A_START = 0.70;   // A, target at power-up (change live: i)
const unsigned long LOG_MS_START   = 250;    // ms per data line     (change live: t)

const uint8_t ADS_ADDR    = 0x48;   // PR33-8: A0 jumper OFF = 0x48, ON = 0x49
const uint8_t ADS_CHANNEL = 0;      // 0 = IN1/CIN1, 1 = IN2/CIN2 (confirm with 'a')
const float   P_LO_INWC   = 0.0;    // pressure at  4 mA (DPTA-20-10)
const float   P_HI_INWC   = 10.0;   // pressure at 20 mA (DPTA-20-10)
const float   TRIM_TOL_MA = 0.016;  // trim "OK" band = 0.1 % of the 16 mA span
// ============================================================================


// ================================ MOTOR =====================================
Basicmicro roboclaw(&Serial1, 10000);
const uint8_t ADDR = 0x80;
const long ROBOCLAW_BAUD = 115200;            // must match Motion Studio

// ---- motor model: Pololu 37D 24 V, 100:1 (from datasheet curves) ----
const float R_MOTOR  = 8.7;       // ohms, winding + brushes, cold. Rises ~0.4%/degC as it warms.
const float KE_OUT   = 0.238;     // volts per output-shaft RPM (back-EMF through the gearbox)
const float I_NOLOAD = 0.064;     // A, gearbox/friction current from datasheet fit
const float KT_FIT   = 0.0071;    // A per kg*mm of output torque, from datasheet fit

// Gearbox: exact ratio (25x30x28x28x30)/(10x10x12x12x12) = 1225/12 = 102.0833:1 (Pololu)
const float GEAR_RATIO = 1225.0 / 12.0;
const float COUNTS_PER_OUTPUT_REV = 64.0 * GEAR_RATIO;   // 6533.33 counts per output rev

// Fallback encoder sign, used only if the shaft can't move during the startup check.
const int8_t ENC_SIGN_DEFAULT = -1;
const unsigned long SIGN_DETECT_MS = 500;
const float SIGN_DETECT_RPM = 3.0;

// ---- control settings ----
const float MAX_TARGET_A = 0.70;  // ~118 kg*mm, under the gearbox's 250 kg*mm instantaneous limit
const float RPM_FILTER = 0.5;     // 0-1, higher = less filtering of the speed signal
const int16_t DUTY_MAX = 32767;   // RoboClaw full duty
const unsigned long LOOP_MS = 10; // 100 Hz control loop
const unsigned long VBUS_MS = 200;
const uint8_t RC_FAIL_LIMIT = 3;  // missed voltage reads in a row = RoboClaw offline (~0.6 s)

float targetA = TARGET_A_START;
bool running = false;
bool autoStartPending = AUTOSTART;

float vbus = 24.0;        // latest bus voltage (V)
bool rcOk = false;        // RoboClaw answering
uint8_t rcFails = 0;
float rpmFilt = 0;        // filtered output RPM, + = driving direction
float dutyFrac = 0;       // 0..1 applied duty
bool saturated = false;

int8_t encSign = ENC_SIGN_DEFAULT;
bool signLocked = false;
unsigned long startMs = 0;
unsigned long lastLoop = 0, lastVbus = 0;
uint32_t lastRcStatus = 0;
uint8_t statusDiv = 0;


// ============================== DPTA / ADC ==================================
// Nominal receiver transfer function, from NCD's published numbers at PGA
// +/-2.048 V (~6430 counts @ 4 mA, ~32154 counts @ 20 mA => ~100.5 mV/mA).
// This sketch runs the ADS1115 at +/-4.096 V for headroom above 20 mA, so the
// counts are half of NCD's numbers. 1 count = 125 uV = ~1.24 uA = ~0.0008 in.w.c.
const float NOM_SLOPE  = 16.0 / ((32154.0 - 6430.0) / 2.0);  // mA per count
const float NOM_OFFSET = 4.0 - (6430.0 / 2.0) * NOM_SLOPE;   // mA

struct Cal {
  uint32_t magic;
  float slope;    // mA per count
  float offset;   // mA
};
const uint32_t CAL_MAGIC = 0x44505441UL;  // "DPTA"  (same EEPROM layout as the old sketch)
Cal  cal;
bool calFromDmm = false;
float spanRefInwc = P_HI_INWC;   // pressure applied during span trim

// background sampler state
bool          adcBusy    = false;   // a conversion is in progress
uint8_t       adcCh      = ADS_CHANNEL;
unsigned long adcT0      = 0;       // micros() when it started
bool          adcFault   = false;
unsigned long adcRetryAt = 0;

// running average for the current data line
long     accSum  = 0;
uint16_t accN    = 0;
bool     accClip = false;
bool     adcWarned = false;


// =============================== OUTPUT =====================================
enum Mode { STREAM, ZERO_TRIM, SPAN_TRIM, PAUSED };
Mode mode = STREAM;
unsigned long logMs = LOG_MS_START;
unsigned long lastLog = 0;
bool runActive = false;
unsigned long runT0 = 0;

// Command line input
const uint8_t LINE_MAX = 40;
char lineBuf[LINE_MAX + 1];
uint8_t lineLen = 0;
unsigned long lastCharMs = 0;


// ------------------------------ small helpers -------------------------------
void hash() { Serial.print(F("# ")); }
void msg(const __FlashStringHelper *s) { hash(); Serial.println(s); }

void printSigned(float v, uint8_t dp) {
  if (v >= 0) Serial.print('+');
  Serial.print(v, dp);
}

char *trim(char *s) {
  while (*s == ' ' || *s == '\t') s++;
  char *e = s + strlen(s);
  while (e > s && (e[-1] == ' ' || e[-1] == '\t')) *--e = 0;
  return s;
}


// =========================== MOTOR FUNCTIONS ================================
void writeDuty(float frac) {
  roboclaw.DutyM1(ADDR, (int16_t)(constrain(frac, 0.0, 1.0) * DUTY_MAX));
}

void stopMotor() {
  running = false;
  dutyFrac = 0;
  saturated = false;
  roboclaw.DutyM1(ADDR, 0);
}

bool startMotor() {
  if (running) { msg(F("Motor already ON")); return true; }
  if (!rcOk) {
    msg(F("RoboClaw not answering - motor NOT started (check main power / wiring)"));
    return false;
  }
  rpmFilt = 0; dutyFrac = 0; saturated = false;
  signLocked = false;
  startMs = millis();
  running = true;
  hash(); Serial.print(F("Motor ON (torque mode), target ")); Serial.print(targetA, 2); Serial.println(F(" A"));
  return true;
}

// Reads the bus voltage; also tells us whether the RoboClaw is there at all.
void checkRoboClaw() {
  bool ok;
  uint16_t raw = roboclaw.ReadMainBatteryVoltage(ADDR, &ok);
  if (ok && raw > 60) {                     // > 6 V = RoboClaw minimum
    vbus = raw / 10.0;
    rcFails = 0;
    if (!rcOk) {
      rcOk = true;
      hash(); Serial.print(F("RoboClaw answering, Vbus ")); Serial.print(vbus, 1); Serial.println(F(" V"));
    }
    return;
  }
  if (rcFails < RC_FAIL_LIMIT && ++rcFails == RC_FAIL_LIMIT) {   // just went offline
    rcOk = false;
    if (running) {
      stopMotor();
      msg(F("RoboClaw stopped answering -> motor OFF (send 1 or g once it is back)"));
    } else {
      msg(F("RoboClaw not answering - check main power, S1/S2 wiring, Packet Serial 0x80 @ 115200"));
    }
  }
}

void lockSign(int8_t s, bool detected) {
  encSign = s;
  signLocked = true;
  if (detected) {
    hash(); Serial.print(F("Encoder direction detected: "));
    Serial.println(s > 0 ? F("normal (+1)") : F("inverted (-1)"));
  } else {
    hash(); Serial.print(F("No shaft motion during startup check (held?). Using default encoder sign "));
    Serial.println(s);
    bool ok2; uint8_t st;
    int32_t q2 = (int32_t)roboclaw.ReadSpeedM2(ADDR, &st, &ok2);
    if (ok2 && q2 != 0)
      msg(F("!! Encoder 2 is counting: untick 'Swap Encoder Channels' in Motion Studio."));
    else
      msg(F("   If the shaft was free and didn't turn, check motor + encoder wiring."));
  }
}

void controlStep() {
  bool ok; uint8_t st;
  int32_t qpps = (int32_t)roboclaw.ReadSpeedM1(ADDR, &st, &ok);   // signed counts/s
  float rpmRaw = (qpps * 60.0) / COUNTS_PER_OUTPUT_REV;

  if (!signLocked) {
    // Startup: drive at stall duty and see which way the encoder counts.
    if (ok && fabs(rpmRaw) > SIGN_DETECT_RPM) lockSign(rpmRaw > 0 ? 1 : -1, true);
    else if (millis() - startMs > SIGN_DETECT_MS) lockSign(ENC_SIGN_DEFAULT, false);
    rpmFilt = 0;                    // treat as stalled until the sign is known
  } else if (ok) {
    rpmFilt += RPM_FILTER * (encSign * rpmRaw - rpmFilt);
  }

  float vNeeded = targetA * R_MOTOR + KE_OUT * rpmFilt;
  float frac = vNeeded / vbus;
  saturated = (frac >= 1.0);
  dutyFrac = constrain(frac, 0.0, 1.0);   // never reverse voltage
  writeDuty(dutyFrac);
}

// While stopped: keep reading speed so the coast-down shows in the data.
void readRpmIdle() {
  bool ok; uint8_t st;
  int32_t qpps = (int32_t)roboclaw.ReadSpeedM1(ADDR, &st, &ok);
  if (ok) rpmFilt += RPM_FILTER * (encSign * (qpps * 60.0) / COUNTS_PER_OUTPUT_REV - rpmFilt);
}

// Call as often as possible. Never blocks for long (unless the RoboClaw is
// unplugged mid-run: each failed packet then waits for its timeout).
void serviceMotor() {
  unsigned long now = millis();
  if (now - lastVbus >= VBUS_MS) {
    lastVbus = now;
    checkRoboClaw();
    if (rcOk && ++statusDiv >= 5) {          // once a second: report status word changes
      statusDiv = 0;
      bool v;
      uint32_t e = roboclaw.ReadError(ADDR, &v);
      if (v && e != lastRcStatus) {
        lastRcStatus = e;
        hash(); Serial.print(F("RoboClaw status word 0x")); Serial.print(e, HEX);
        Serial.println(e ? F("  (non-zero: see the error/warning table in the RoboClaw manual)") : F("  (back to OK)"));
      }
    }
    if (autoStartPending && rcOk) {
      autoStartPending = false;
      msg(F("AUTOSTART: RoboClaw found, starting motor"));
      startMotor();
    }
  }
  if (now - lastLoop >= LOOP_MS) {
    lastLoop = now;
    if (running) controlStep();
    else if (rcOk) { roboclaw.DutyM1(ADDR, 0); readRpmIdle(); }  // keep-alive for the serial timeout
  }
}

void waitServiced(unsigned long ms) {
  unsigned long t0 = millis();
  while (millis() - t0 < ms) serviceMotor();
}


// ============================ ADS1115 FUNCTIONS =============================
bool adsWrite16(uint8_t reg, uint16_t val) {
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)(val >> 8));
  Wire.write((uint8_t)(val & 0xFF));
  return Wire.endTransmission() == 0;
}

bool adsRead16(uint8_t reg, uint16_t &val) {
  Wire.beginTransmission(ADS_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission() != 0) return false;
  if (Wire.requestFrom(ADS_ADDR, (uint8_t)2) != 2) return false;
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  val = ((uint16_t)hi << 8) | lo;
  return true;
}

bool adsPresent() {
  Wire.beginTransmission(ADS_ADDR);
  return Wire.endTransmission() == 0;
}

void adcFail() {
  adcBusy = false;
  adcFault = true;
  adcRetryAt = millis() + 100;
}

// Start one single-shot conversion of AINch vs GND, +/-4.096 V, 128 SPS.
void adcStart() {
  uint16_t cfg = 0x8000                          // OS: start a conversion
               | ((uint16_t)(0x4 + adcCh) << 12) // MUX: AINch vs GND
               | ((uint16_t)0x1 << 9)            // PGA: +/-4.096 V
               | 0x0100                          // MODE: single-shot
               | ((uint16_t)0x4 << 5)            // DR: 128 SPS
               | 0x0003;                         // comparator off
  if (adsWrite16(0x01, cfg)) { adcBusy = true; adcT0 = micros(); }
  else adcFail();
}

// Non-blocking. Returns true when a new sample is in 'counts'.
// restart = immediately start the next conversion on the same channel.
bool adcPoll(int16_t &counts, bool restart) {
  if (!adcBusy) {
    if ((long)(millis() - adcRetryAt) >= 0) adcStart();
    return false;
  }
  if (micros() - adcT0 < 7800UL) return false;   // ~7.8 ms per conversion at 128 SPS
  uint16_t status;
  if (!adsRead16(0x01, status)) { adcFail(); return false; }
  if (!(status & 0x8000)) {                      // OS = 0: still converting
    if (micros() - adcT0 > 50000UL) adcFail();
    return false;
  }
  uint16_t raw;
  if (!adsRead16(0x00, raw)) { adcFail(); return false; }
  counts = (int16_t)raw;
  adcBusy = false;
  adcFault = false;
  if (restart) adcStart();
  return true;
}

// Let an in-flight conversion finish and throw it away.
void adcFlush() {
  int16_t dummy;
  unsigned long t0 = millis();
  while (adcBusy && millis() - t0 < 60) {
    serviceMotor();
    adcPoll(dummy, false);
  }
  adcBusy = false;
}

void resetAverage() {
  accSum = 0; accN = 0; accClip = false;
  lastLog = millis();
}

// Blocking average of n samples on channel ch (the motor keeps running).
float averageCounts(uint8_t ch, uint16_t n, bool &clip, bool &err) {
  adcFlush();
  adcCh = ch;
  long sum = 0;
  clip = false; err = false;
  for (uint16_t i = 0; i < n && !err; i++) {
    int16_t c;
    bool got = false;
    unsigned long t0 = millis();
    while (millis() - t0 < 100) {
      serviceMotor();
      if (adcPoll(c, true)) { got = true; break; }
    }
    if (!got) { err = true; break; }
    if (c >= 32767) clip = true;
    sum += c;
  }
  adcFlush();
  adcCh = ADS_CHANNEL;
  resetAverage();
  return err ? NAN : (float)sum / n;
}

// ---------------------------- Conversions -----------------------------------
float countsToMA(float c)  { return cal.slope * c + cal.offset; }
float maToInwc(float mA)   { return P_LO_INWC + (mA - 4.0) * (P_HI_INWC - P_LO_INWC) / 16.0; }
float inwcToMA(float p)    { return 4.0 + 16.0 * (p - P_LO_INWC) / (P_HI_INWC - P_LO_INWC); }

// --------------------------- Calibration storage ----------------------------
void useNominal() {
  cal.magic  = CAL_MAGIC;
  cal.slope  = NOM_SLOPE;
  cal.offset = NOM_OFFSET;
  calFromDmm = false;
}

void loadCal() {
  EEPROM.get(0, cal);
  if (cal.magic != CAL_MAGIC || isnan(cal.slope) || isnan(cal.offset) ||
      fabs(cal.slope / NOM_SLOPE - 1.0) > 0.10) {
    useNominal();
  } else {
    calFromDmm = true;
  }
}

void resetCal() {
  Cal blank = {0, 0, 0};
  EEPROM.put(0, blank);
  useNominal();
  msg(F("Receiver calibration reset to NOMINAL."));
}


// ============================== SERIAL INPUT ================================
// Non-blocking. Returns true when a complete line is in lineBuf.
bool pollLine() {
  while (Serial.available()) {
    char ch = Serial.read();
    lastCharMs = millis();
    if (ch == '\n' || ch == '\r') {
      if (lineLen) { lineBuf[lineLen] = 0; lineLen = 0; return true; }
    } else if (lineLen < LINE_MAX) {
      lineBuf[lineLen++] = ch;
    }
  }
  // Serial Monitor set to "No line ending": a 50 ms pause ends the line.
  if (lineLen && millis() - lastCharMs > 50) { lineBuf[lineLen] = 0; lineLen = 0; return true; }
  return false;
}

// Wait for a typed line; the motor control keeps running meanwhile.
char *promptLine() {
  while (!pollLine()) serviceMotor();
  return trim(lineBuf);
}

bool askYes(const __FlashStringHelper *q) {
  hash(); Serial.print(q); Serial.println(F(" (y/n)"));
  char *s = promptLine();
  return s[0] == 'y' || s[0] == 'Y';
}


// ================================ DISPLAY ===================================
void printHeader() {
  Serial.println(F("t_s,motor_on,rpm,dp_inwc"));
}

void printTime(unsigned long ms) {   // exact seconds with 3 decimals, no float rounding
  Serial.print(ms / 1000);
  Serial.print('.');
  unsigned int r = ms % 1000;
  if (r < 100) Serial.print('0');
  if (r < 10)  Serial.print('0');
  Serial.print(r);
}

void comma() { Serial.print(','); }

void printDataLine(uint16_t n, float counts) {
  printTime(millis() - runT0);         comma();
  Serial.print(running ? 1 : 0);       comma();
  Serial.print(rpmFilt, 1);            comma();
  if (n) Serial.print(maToInwc(countsToMA(counts)), 3);   // blank if no readings
  Serial.println();
}

// Bar graph of trim error, +/-0.20 mA full width.
void printBar(float err) {
  const int   HALF = 10;
  const float FULL = 0.20;
  float x = err / FULL * HALF;
  int pos = (int)(x + (x >= 0 ? 0.5 : -0.5));
  if (pos >  HALF) pos =  HALF;
  if (pos < -HALF) pos = -HALF;
  Serial.print('[');
  for (int i = -HALF; i <= HALF; i++) Serial.print(i == pos ? '#' : (i == 0 ? '|' : '-'));
  Serial.print(']');
}

void printTrim(const __FlashStringHelper *label, float mA, float target, bool clip) {
  float err  = mA - target;
  float errP = err * (P_HI_INWC - P_LO_INWC) / 16.0;
  hash();
  Serial.print(label);
  Serial.print(F("  I "));       Serial.print(mA, 3);
  Serial.print(F(" mA  tgt "));  Serial.print(target, 3);
  Serial.print(F("  err "));     printSigned(err, 3);
  Serial.print(F(" mA ("));      printSigned(errP, 3);
  Serial.print(F(" in.w.c.)  "));
  printBar(err);
  if (clip)                          Serial.print(F("  ADC CLIPPED"));
  else if (fabs(err) <= TRIM_TOL_MA) Serial.print(F("  << OK"));
  else if (err > 0)                  Serial.print(F("  -> turn output DOWN"));
  else                               Serial.print(F("  -> turn output UP"));
  Serial.println();
}

// Called once per data period with the samples collected since the last call.
void outputTick() {
  long sum = accSum; uint16_t n = accN; bool clip = accClip;
  accSum = 0; accN = 0; accClip = false;
  float counts = n ? (float)sum / n : 0;

  if (n == 0 && !adcWarned && mode != PAUSED) {
    adcWarned = true;
    msg(F("No ADC samples - I2C error? Check wiring, address jumper, pull-ups"));
  }
  if (n) adcWarned = false;

  switch (mode) {
    case STREAM:
      printDataLine(n, counts);
      break;
    case ZERO_TRIM:
      if (n) printTrim(F("ZERO"), countsToMA(counts), 4.0, clip);
      break;
    case SPAN_TRIM:
      if (n) printTrim(F("SPAN"), countsToMA(counts), inwcToMA(spanRefInwc), clip);
      break;
    default:
      break;
  }
}

void printHelp() {
  msg(F("=== DPTA pressure + RoboClaw torque logger ==="));
  hash();
  Serial.print(F("Motor "));          Serial.print(running ? F("ON") : F("OFF"));
  Serial.print(F(", target "));       Serial.print(targetA, 2);
  Serial.print(F(" A, RoboClaw "));   Serial.print(rcOk ? F("OK") : (rcFails < RC_FAIL_LIMIT ? F("checking...") : F("not answering")));
  Serial.print(F(", run "));          Serial.print(runActive ? F("ACTIVE") : F("none"));
  Serial.print(F(", data every "));   Serial.print(logMs); Serial.println(F(" ms"));
  hash();
  Serial.print(F("ADS1115 0x"));      Serial.print(ADS_ADDR, HEX);
  Serial.print(F(" AIN"));            Serial.print(ADS_CHANNEL);
  Serial.print(F(", "));              Serial.print(P_LO_INWC, 2);
  Serial.print(F(" to "));            Serial.print(P_HI_INWC, 2);
  Serial.print(F(" in.w.c., receiver cal "));
  Serial.print(calFromDmm ? F("DMM (EEPROM)") : F("NOMINAL"));
  Serial.print(F(" slope "));         Serial.print(cal.slope * 1000.0, 5);
  Serial.print(F(" uA/count offset ")); printSigned(cal.offset, 4);
  Serial.println(F(" mA"));
  msg(F("g label = start run + motor    x = stop motor + end run"));
  msg(F("1 / 0 = motor on / off (run continues)    i0.70 = target A    t250 = data period ms"));
  msg(F("m = data on    p = pause data    z / s = zero / span trim    r = span ref pressure"));
  msg(F("c = calibrate receiver vs DMM    n = reset receiver cal    a = scan ADC inputs    h = help"));
}


// ============================= DPTA UTILITIES ===============================
bool calPoint(uint8_t n, float &counts, float &mA) {
  hash(); Serial.print(F("Point ")); Serial.print(n);
  Serial.println(F(": when the DMM is steady, type its reading in mA + Enter (x = abort)"));
  char *s = promptLine();
  if (s[0] == 'x' || s[0] == 'X') { msg(F("aborted")); return false; }
  mA = atof(s);
  hash(); Serial.print(F("  DMM = ")); Serial.print(mA, 3); Serial.println(F(" mA"));
  if (mA < 3.5 || mA > 21.5) { msg(F("  Value outside 3.5-21.5 mA. Aborted.")); return false; }
  msg(F("  Sampling ADC, hold steady (~1 s)..."));
  bool clip, err;
  counts = averageCounts(ADS_CHANNEL, 128, clip, err);
  if (err)  { msg(F("  I2C error. Aborted.")); return false; }
  if (clip) { msg(F("  ADC clipped. Aborted.")); return false; }
  hash(); Serial.print(F("  counts = ")); Serial.println(counts, 1);
  return true;
}

void receiverCal() {
  Mode prev = mode;
  mode = PAUSED;
  msg(F("=== RECEIVER CALIBRATION ==="));
  msg(F("DMM in series with the loop on its mA range (between DPTA '-' and G)."));
  msg(F("Point 1: low current - HI and LO ports tied together (~4 mA)."));
  float c1, m1, c2, m2;
  if (!calPoint(1, c1, m1)) { mode = prev; resetAverage(); return; }
  msg(F("Point 2: high current - apply and HOLD a steady pressure near full scale (~16-20 mA)."));
  if (!calPoint(2, c2, m2)) { mode = prev; resetAverage(); return; }

  if (m2 - m1 < 8.0) {
    msg(F("Points must be at least 8 mA apart. Aborted."));
    mode = prev; resetAverage(); return;
  }
  float slope  = (m2 - m1) / (c2 - c1);
  float offset = m1 - slope * c1;
  float dev    = (slope / NOM_SLOPE - 1.0) * 100.0;

  hash();
  Serial.print(F("New slope "));  Serial.print(slope * 1000.0, 5);
  Serial.print(F(" uA/count ("));  printSigned(dev, 2);
  Serial.print(F(" % vs nominal), offset ")); printSigned(offset, 4);
  Serial.println(F(" mA"));
  if (fabs(dev) > 5.0) {
    msg(F("WARNING: >5 % from nominal. Check wiring, DMM range, and that the"));
    msg(F("pressure held steady. Not recommended to save."));
  }
  if (askYes(F("Save to EEPROM?"))) {
    cal.magic = CAL_MAGIC; cal.slope = slope; cal.offset = offset;
    EEPROM.put(0, cal);
    calFromDmm = true;
    msg(F("Saved."));
  } else {
    msg(F("Not saved."));
  }
  mode = prev;
  resetAverage();
}

void setSpanRef() {
  Mode prev = mode;
  mode = PAUSED;
  msg(F("Reference pressure applied to HI port, in.w.c. (e.g. 10.00):"));
  float p = atof(promptLine());
  hash(); Serial.println(p, 3);
  float span = P_HI_INWC - P_LO_INWC;
  if (p < P_LO_INWC + 0.5 * span || p > P_HI_INWC + 0.05 * span) {
    msg(F("Use 50-105 % of range for span trimming. Not changed."));
  } else {
    spanRefInwc = p;
    hash(); Serial.print(F("Span target is now ")); Serial.print(inwcToMA(p), 3); Serial.println(F(" mA"));
  }
  mode = prev;
  adcFlush();
  resetAverage();
}

void scanChannels() {
  msg(F("ADS1115 inputs (single-ended, current receiver cal applied):"));
  for (uint8_t ch = 0; ch < 4; ch++) {
    bool clip, err;
    float c = averageCounts(ch, 8, clip, err);
    hash(); Serial.print(F("  AIN")); Serial.print(ch); Serial.print(F(": "));
    if (err) { Serial.println(F("I2C error")); continue; }
    Serial.print(c, 1); Serial.print(F(" counts  ~"));
    Serial.print(countsToMA(c), 3); Serial.println(F(" mA"));
  }
}

void i2cScan() {
  hash(); Serial.print(F("I2C devices found:"));
  bool any = false;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.print(F(" 0x")); Serial.print(a, HEX);
      any = true;
    }
  }
  if (!any) Serial.print(F(" none (check SDA/SCL, 5V, GND, pull-up jumpers)"));
  Serial.println();
}


// ================================ RUNS ======================================
void startRun(const char *label) {
  if (runActive) Serial.println(F("# RUN END"));     // a new g closes the previous run
  runActive = true;
  runT0 = millis();
  Serial.print(F("# RUN START "));                   // the PC logger opens a new file here
  Serial.println(label);
  mode = STREAM;
  printHeader();
  autoStartPending = false;
  if (!running) startMotor();
  resetAverage();
}

void endRun() {
  autoStartPending = false;
  stopMotor();
  msg(F("Motor OFF"));
  if (runActive) {
    runActive = false;
    Serial.println(F("# RUN END"));                  // the PC logger saves the file here
  } else {
    msg(F("(no run was active - g starts one)"));
  }
}


// =============================== COMMANDS ===================================
void handleCommand(char *line) {
  char *s = trim(line);
  if (!*s) return;
  char c = s[0];
  char *arg = trim(s + 1);

  switch (c) {
    case 'g': case 'G': startRun(arg); break;
    case 'x': case 'X': endRun(); break;
    case '1':
      autoStartPending = false;
      startMotor();
      break;
    case '0':
      autoStartPending = false;   // stays off until 1 / g or a reset
      stopMotor();
      msg(F("Motor OFF (send 1 to restart)"));
      break;
    case 'i': case 'I':
      if (*arg) targetA = constrain(atof(arg), 0.0, MAX_TARGET_A);
      hash(); Serial.print(F("Target ")); Serial.print(targetA, 2); Serial.println(F(" A"));
      break;
    case 't': case 'T': {
      long v = atol(arg);
      if (v >= 50 && v <= 5000) logMs = v;
      else msg(F("Data period must be 50 - 5000 ms"));
      hash(); Serial.print(F("Data every ")); Serial.print(logMs); Serial.println(F(" ms"));
      resetAverage();
      break;
    }
    case 'm': case 'M':
      mode = STREAM;
      printHeader();
      resetAverage();
      break;
    case 'p': case 'P':
      mode = PAUSED;
      msg(F("data paused (m = resume). The motor is not affected."));
      break;
    case 'z': case 'Z':
      mode = ZERO_TRIM;
      msg(F("ZERO TRIM: tie HI and LO ports together, adjust ZERO pot to 4.000 mA (m = back to data)"));
      resetAverage();
      break;
    case 's': case 'S':
      mode = SPAN_TRIM;
      hash(); Serial.print(F("SPAN TRIM: apply ")); Serial.print(spanRefInwc, 3);
      Serial.print(F(" in.w.c. to HI (LO open), adjust SPAN pot to "));
      Serial.print(inwcToMA(spanRefInwc), 3); Serial.println(F(" mA (m = back to data)"));
      resetAverage();
      break;
    case 'r': case 'R': setSpanRef();   break;
    case 'c': case 'C': receiverCal();  break;
    case 'n': case 'N': resetCal();     break;
    case 'a': case 'A': scanChannels(); break;
    case 'h': case 'H': case '?': printHelp(); break;
    default:
      msg(F("Unknown command (h = help)"));
      break;
  }
}


// ================================ ARDUINO ===================================
void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) {}   // give a Serial Monitor a moment to attach

  roboclaw.begin(ROBOCLAW_BAUD);
  stopMotor();

  Wire.begin();
  Wire.setClock(100000);
  Wire.setWireTimeout(25000, true);   // a stuck I2C bus can't freeze the motor loop

  loadCal();

  if (!adsPresent()) {
    hash(); Serial.print(F("ADS1115 not found at 0x")); Serial.println(ADS_ADDR, HEX);
    i2cScan();
  }
  printHelp();
  printHeader();
  runT0 = millis();
  resetAverage();
}

void loop() {
  if (pollLine()) handleCommand(lineBuf);

  serviceMotor();

  int16_t c;
  if (adcPoll(c, true)) {
    accSum += c;
    accN++;
    if (c >= 32767) accClip = true;
  }

  unsigned long now = millis();
  if (now - lastLog >= logMs) {
    lastLog = now;
    outputTick();
  }
}
