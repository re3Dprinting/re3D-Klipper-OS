/*
  DPTA_4-20mA_Reader.ino
  ----------------------------------------------------------------------------
  ProSense DPTA-20-10 (0-10 in. w.c., 2-wire 4-20 mA)
    -> NCD PR33-8 (INA196 current-shunt monitor + ADS1115 16-bit ADC, I2C)
    -> Arduino Leonardo

  Serial Monitor: 115200 baud, line ending = "Newline"

  WIRING SUMMARY (details in the write-up):
    PR33-8 I2C IN  GND  -> Leonardo GND
    PR33-8 I2C IN  +5V  -> Leonardo 5V
    PR33-8 I2C IN  SDA  -> Leonardo SDA (D2, or the SDA pin next to AREF)
    PR33-8 I2C IN  SCL  -> Leonardo SCL (D3, or the SCL pin next to AREF)
    PR33-8 jumpers: both "4.7K PULL" installed, A0 removed (address 0x48)

    LOOP IS POWERED BY THE PR33-8 (on-board 16 V boost sources current out of IN1).
    Do NOT add an external loop supply -- it back-drives the 16 V rail.
    PR33-8 IN1 (CIN)       -> DPTA "+"
    DPTA "-"               -> [DMM in mA mode, only while calibrating] -> PR33-8 G (next to IN1)
    Loop budget: ~15.9 V available, DPTA needs 12 V + 0.022 A x R_loop -> ~170 ohm max extra.

  COMMANDS (type a letter, press Enter):
    m  monitor: counts, mA, in.w.c., % of range
    z  ZERO trim helper  (target 4.000 mA, ports tied together)
    s  SPAN trim helper  (target 20.000 mA at the reference pressure)
    r  set the span reference pressure you are actually applying
    c  calibrate the RECEIVER against a DMM in series (2 points, saved to EEPROM)
    n  reset receiver calibration to nominal
    a  scan all four ADS1115 inputs (find which one is your channel)
    p  pause output
    h  help
  ----------------------------------------------------------------------------
*/

#include <Wire.h>
#include <EEPROM.h>

// ============================ USER SETTINGS =================================
const uint8_t ADS_ADDR    = 0x48;   // PR33-8: A0 jumper OFF = 0x48, ON = 0x49
const uint8_t ADS_CHANNEL = 0;      // 0 = IN1/CIN1, 1 = IN2/CIN2 (confirm with 'a')
const float   P_LO_INWC   = 0.0;    // pressure at  4 mA (DPTA-20-10)
const float   P_HI_INWC   = 10.0;   // pressure at 20 mA (DPTA-20-10)
const uint8_t N_AVG       = 32;     // ADC samples averaged per printed reading
const float   TRIM_TOL_MA = 0.016;  // trim "OK" band = 0.1 % of the 16 mA span
// ============================================================================

// Nominal receiver transfer function, from NCD's published numbers at PGA
// +/-2.048 V (~6430 counts @ 4 mA, ~32154 counts @ 20 mA  => ~100.5 mV/mA,
// i.e. ~5 ohm shunt x INA196 gain of 20).
// This sketch runs the ADS1115 at +/-4.096 V so there is headroom above
// 20 mA (at +/-2.048 V the ADC clips at ~20.4 mA, which gets in the way while
// turning the span pot). Counts are therefore half of NCD's numbers.
// 1 count = 125 uV = ~1.24 uA = ~0.0008 in.w.c. on a 0-10" transmitter.
const float NOM_SLOPE  = 16.0 / ((32154.0 - 6430.0) / 2.0);  // mA per count
const float NOM_OFFSET = 4.0 - (6430.0 / 2.0) * NOM_SLOPE;   // mA

struct Cal {
  uint32_t magic;
  float slope;    // mA per count
  float offset;   // mA
};
const uint32_t CAL_MAGIC = 0x44505441UL;  // "DPTA"
Cal  cal;
bool calFromDmm = false;

enum Mode { MONITOR, ZERO_TRIM, SPAN_TRIM, PAUSED };
Mode  mode        = MONITOR;
float spanRefInwc = P_HI_INWC;   // pressure applied during span trim

bool i2cError = false;
bool clipped  = false;

// ----------------------------- ADS1115 --------------------------------------
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

// One single-shot conversion, input AINch referenced to GND.
bool adsReadOnce(uint8_t ch, int16_t &counts) {
  uint16_t cfg = 0x8000                          // OS: start a conversion
               | ((uint16_t)(0x4 + ch) << 12)    // MUX: AINch vs GND
               | ((uint16_t)0x1 << 9)            // PGA: +/-4.096 V
               | 0x0100                          // MODE: single-shot
               | ((uint16_t)0x4 << 5)            // DR: 128 SPS
               | 0x0003;                         // comparator off
  if (!adsWrite16(0x01, cfg)) return false;
  delay(8);                                      // ~7.8 ms conversion
  uint16_t status = 0;
  unsigned long t0 = millis();
  do {
    if (!adsRead16(0x01, status)) return false;
    if (millis() - t0 > 50) return false;
  } while (!(status & 0x8000));                  // OS=1 -> done
  uint16_t raw;
  if (!adsRead16(0x00, raw)) return false;
  counts = (int16_t)raw;
  return true;
}

// Average n conversions. Sets the global i2cError / clipped flags.
float readCounts(uint8_t ch, uint8_t n) {
  long sum = 0;
  clipped = false;
  for (uint8_t i = 0; i < n; i++) {
    int16_t c;
    if (!adsReadOnce(ch, c)) { i2cError = true; return NAN; }
    if (c >= 32767) clipped = true;
    sum += c;
  }
  i2cError = false;
  return (float)sum / n;
}

// --------------------------- Conversions ------------------------------------
float countsToMA(float c)  { return cal.slope * c + cal.offset; }
float maToInwc(float mA)   { return P_LO_INWC + (mA - 4.0) * (P_HI_INWC - P_LO_INWC) / 16.0; }
float inwcToMA(float p)    { return 4.0 + 16.0 * (p - P_LO_INWC) / (P_HI_INWC - P_LO_INWC); }

// --------------------------- Calibration storage -----------------------------
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
  Serial.println(F("\nReceiver calibration reset to NOMINAL."));
}

// ------------------------------ Serial helpers ------------------------------
void flushInput() {
  delay(20);
  while (Serial.available()) Serial.read();
}

String readLine() {
  String s;
  while (true) {
    if (Serial.available()) {
      char ch = Serial.read();
      if (ch == '\n' || ch == '\r') {
        if (s.length()) { s.trim(); return s; }
      } else {
        s += ch;
      }
    }
  }
}

bool askYes(const __FlashStringHelper *q) {
  Serial.print(q);
  Serial.print(F(" (y/n): "));
  String s = readLine();
  Serial.println(s);
  return s.length() && (s[0] == 'y' || s[0] == 'Y');
}

void printSigned(float v, uint8_t dp) {
  if (v >= 0) Serial.print('+');
  Serial.print(v, dp);
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

// ------------------------------ Display -------------------------------------
void printMonitor(float counts, float mA) {
  Serial.print(F("counts "));  Serial.print(counts, 1);
  Serial.print(F("   I "));    Serial.print(mA, 3);
  Serial.print(F(" mA   P ")); Serial.print(maToInwc(mA), 3);
  Serial.print(F(" in.w.c.   "));
  Serial.print((mA - 4.0) / 16.0 * 100.0, 1);
  Serial.print('%');
  if (clipped)          Serial.print(F("   ADC CLIPPED"));
  else if (mA > 20.5)   Serial.print(F("   ABOVE RANGE"));
  else if (mA < 3.8)    Serial.print(F("   BELOW 4 mA (open loop / reversed / under-range?)"));
  Serial.println();
}

void printTrim(const __FlashStringHelper *label, float mA, float target) {
  float err  = mA - target;
  float errP = err * (P_HI_INWC - P_LO_INWC) / 16.0;
  Serial.print(label);
  Serial.print(F("  I "));       Serial.print(mA, 3);
  Serial.print(F(" mA  tgt "));  Serial.print(target, 3);
  Serial.print(F("  err "));     printSigned(err, 3);
  Serial.print(F(" mA ("));      printSigned(errP, 3);
  Serial.print(F(" in.w.c.)  "));
  printBar(err);
  if (clipped)                     Serial.print(F("  ADC CLIPPED"));
  else if (fabs(err) <= TRIM_TOL_MA) Serial.print(F("  << OK"));
  else if (err > 0)                Serial.print(F("  -> turn output DOWN"));
  else                             Serial.print(F("  -> turn output UP"));
  Serial.println();
}

void printHelp() {
  Serial.println();
  Serial.println(F("=== DPTA 4-20 mA reader (PR33-8 / ADS1115) ==="));
  Serial.print(F("ADS1115 addr 0x")); Serial.print(ADS_ADDR, HEX);
  Serial.print(F(", channel AIN"));   Serial.print(ADS_CHANNEL);
  Serial.print(F(", range "));        Serial.print(P_LO_INWC, 2);
  Serial.print(F(" to "));            Serial.print(P_HI_INWC, 2);
  Serial.println(F(" in.w.c."));
  Serial.print(F("Receiver cal: "));
  Serial.print(calFromDmm ? F("DMM (EEPROM)") : F("NOMINAL"));
  Serial.print(F("  slope "));  Serial.print(cal.slope * 1000.0, 5);
  Serial.print(F(" uA/count  offset ")); printSigned(cal.offset, 4);
  Serial.println(F(" mA"));
  Serial.println(F("Commands: m=monitor  z=zero trim  s=span trim  r=set span ref pressure"));
  Serial.println(F("          c=calibrate receiver vs DMM  n=reset receiver cal"));
  Serial.println(F("          a=scan ADC inputs  p=pause  h=help"));
  Serial.println();
}

// --------------------------- Receiver calibration ----------------------------
bool calPoint(uint8_t n, float &counts, float &mA) {
  Serial.print(F("  Point ")); Serial.print(n);
  Serial.print(F(": when the DMM is steady, type its reading in mA + Enter (x = abort): "));
  String s = readLine();
  if (s == "x" || s == "X") { Serial.println(F("aborted")); return false; }
  mA = s.toFloat();
  Serial.println(mA, 3);
  if (mA < 3.5 || mA > 21.5) {
    Serial.println(F("  Value outside 3.5-21.5 mA. Aborted."));
    return false;
  }
  Serial.println(F("  Sampling ADC, hold steady (~1 s)..."));
  counts = readCounts(ADS_CHANNEL, 128);
  if (i2cError) { Serial.println(F("  I2C error. Aborted.")); return false; }
  if (clipped)  { Serial.println(F("  ADC clipped. Aborted.")); return false; }
  Serial.print(F("  counts = ")); Serial.println(counts, 1);
  return true;
}

void receiverCal() {
  Mode prev = mode;
  mode = PAUSED;
  flushInput();
  Serial.println(F("\n=== RECEIVER CALIBRATION ==="));
  Serial.println(F("DMM in series with the loop on its mA range (between DPTA '-' and G)."));
  Serial.println(F("Point 1: low current - HI and LO ports tied together (~4 mA)."));
  float c1, m1, c2, m2;
  if (!calPoint(1, c1, m1)) { mode = prev; return; }
  Serial.println(F("Point 2: high current - apply and HOLD a steady pressure near full scale (~16-20 mA)."));
  if (!calPoint(2, c2, m2)) { mode = prev; return; }

  if (m2 - m1 < 8.0) {
    Serial.println(F("Points must be at least 8 mA apart. Aborted."));
    mode = prev; return;
  }
  float slope  = (m2 - m1) / (c2 - c1);
  float offset = m1 - slope * c1;
  float dev    = (slope / NOM_SLOPE - 1.0) * 100.0;

  Serial.print(F("New slope "));  Serial.print(slope * 1000.0, 5);
  Serial.print(F(" uA/count ("));  printSigned(dev, 2);
  Serial.print(F(" % vs nominal), offset ")); printSigned(offset, 4);
  Serial.println(F(" mA"));
  if (fabs(dev) > 5.0) {
    Serial.println(F("WARNING: >5 % from nominal. Check wiring, DMM range, and that the"));
    Serial.println(F("pressure held steady. Not recommended to save."));
  }
  if (askYes(F("Save to EEPROM?"))) {
    cal.magic = CAL_MAGIC; cal.slope = slope; cal.offset = offset;
    EEPROM.put(0, cal);
    calFromDmm = true;
    Serial.println(F("Saved."));
  } else {
    Serial.println(F("Not saved."));
  }
  mode = prev;
}

void setSpanRef() {
  Mode prev = mode;
  mode = PAUSED;
  flushInput();
  Serial.print(F("\nReference pressure applied to HI port, in.w.c. (e.g. 10.00): "));
  String s = readLine();
  float p = s.toFloat();
  Serial.println(p, 3);
  float span = P_HI_INWC - P_LO_INWC;
  if (p < P_LO_INWC + 0.5 * span || p > P_HI_INWC + 0.05 * span) {
    Serial.println(F("Use 50-105 % of range for span trimming. Not changed."));
  } else {
    spanRefInwc = p;
    Serial.print(F("Span target is now ")); Serial.print(inwcToMA(p), 3); Serial.println(F(" mA"));
  }
  mode = prev;
}

void scanChannels() {
  Serial.println(F("\nADS1115 inputs (single-ended, current receiver cal applied):"));
  for (uint8_t ch = 0; ch < 4; ch++) {
    float c = readCounts(ch, 8);
    Serial.print(F("  AIN")); Serial.print(ch); Serial.print(F(": "));
    if (i2cError) { Serial.println(F("I2C error")); continue; }
    Serial.print(c, 1); Serial.print(F(" counts  ~"));
    Serial.print(countsToMA(c), 3); Serial.println(F(" mA"));
  }
  Serial.println();
}

void i2cScan() {
  Serial.print(F("I2C devices found:"));
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

// ------------------------------ Commands ------------------------------------
void handleSerial() {
  while (Serial.available()) {
    char ch = Serial.read();
    switch (ch) {
      case 'm': case 'M':
        mode = MONITOR;
        Serial.println(F("\n-- MONITOR --"));
        break;
      case 'z': case 'Z':
        mode = ZERO_TRIM;
        Serial.println(F("\n-- ZERO TRIM: tie HI and LO ports together, adjust ZERO pot to 4.000 mA --"));
        break;
      case 's': case 'S':
        mode = SPAN_TRIM;
        Serial.print(F("\n-- SPAN TRIM: apply ")); Serial.print(spanRefInwc, 3);
        Serial.print(F(" in.w.c. to HI (LO open), adjust SPAN pot to "));
        Serial.print(inwcToMA(spanRefInwc), 3); Serial.println(F(" mA --"));
        break;
      case 'r': case 'R': setSpanRef();   break;
      case 'c': case 'C': receiverCal();  break;
      case 'n': case 'N': resetCal();     break;
      case 'a': case 'A': scanChannels(); break;
      case 'p': case 'P':
        mode = PAUSED;
        Serial.println(F("-- paused (m / z / s to resume) --"));
        break;
      case 'h': case 'H': case '?': printHelp(); break;
      default: break;   // ignore newlines, spaces, etc.
    }
  }
}

// ------------------------------ Arduino -------------------------------------
void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 5000) {}   // Leonardo: wait for USB serial

  Wire.begin();
  Wire.setClock(100000);

  loadCal();

  if (!adsPresent()) {
    Serial.print(F("\nADS1115 not found at 0x")); Serial.println(ADS_ADDR, HEX);
    i2cScan();
  }
  printHelp();
}

void loop() {
  handleSerial();
  if (mode == PAUSED) return;

  float c = readCounts(ADS_CHANNEL, N_AVG);
  if (i2cError) {
    Serial.println(F("I2C read error - check wiring, address jumper, pull-ups"));
    delay(1000);
    return;
  }
  float mA = countsToMA(c);

  switch (mode) {
    case MONITOR:   printMonitor(c, mA); break;
    case ZERO_TRIM: printTrim(F("ZERO"), mA, 4.0); break;
    case SPAN_TRIM: printTrim(F("SPAN"), mA, inwcToMA(spanRefInwc)); break;
    default: break;
  }
}