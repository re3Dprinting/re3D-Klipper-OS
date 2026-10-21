# Contributing to the wiki

[Wiki home](README.md)

## Start with an operator's task

Use titles such as "Connect to Wi-Fi" or "Collect logs for support" rather than
implementation filenames. Keep one primary task per article.

1. Copy the appropriate [how-to](templates/how-to.md),
   [troubleshooting](templates/troubleshooting.md), or
   [material recipe](templates/material-recipe.md) template.
2. Save it under a descriptive lowercase, hyphenated Markdown filename.
3. Replace all bracketed placeholders. Leave the article marked **Draft** until
   review is complete.
4. Link it from the [wiki home](README.md) or the relevant section.
5. Submit the documentation change through the normal repository review process.

## Writing conventions

- Write for customers first; put implementation details in [Advanced](advanced/README.md).
- Use the exact UI labels and explain unfamiliar terms on first use.
- Put prerequisites, data-loss warnings, and machine compatibility before steps.
- Give each action an observable expected result and a clear stop condition.
- Never invent temperatures, offsets, motion limits, release numbers, or support
  contact details. Obtain them from an approved source and identify that source.
- Distinguish existing features from proposals.
- Use relative Markdown links within the repository.

## Screenshots

Add screenshots only when they help complete a task. Store them in an `images`
subdirectory near the article, use meaningful filenames and alt text, and record
the OS version represented. Remove passwords, API keys, customer jobs, and private
network details before committing images.

## Verification before marking a procedure verified

- [ ] Exact machine, controller, options, and OS revision are recorded.
- [ ] An appropriate reviewer has performed the steps on the stated hardware.
- [ ] Expected results and failure/stop conditions match what was observed.
- [ ] Restart, persistence, and backup implications are documented.
- [ ] Links work and screenshots match the procedure.
- [ ] No placeholders or unsupported claims remain.
- [ ] Reviewer and review date are recorded.

Source inspection alone is not hardware validation. Return an article to draft
when a behavior change invalidates its procedure.

## Publishing

This starter uses repository Markdown only. It does not add a site generator,
deploy a public site, publish a GitHub Wiki, or install help pages on the printer.
Choose a publishing destination separately once the content and review process
are established.
