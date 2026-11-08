# Known issues in the Chronikwerk alpha

Thank you for testing Chronikwerk. This page lists what does not work yet, how to work around
it, and how to report problems. Use a disposable Zammad with test data only.

## Not yet confirmed against a live Zammad

Chronikwerk follows Zammad's documented webhook signature, ticket format, tag API, article list,
original-message (`ticket_article_plain`) and attachment endpoints, and the capture-to-package
run is tested against those documented formats. It has not yet been run against a real Zammad
instance. If a tagged ticket does not produce a sealed package, please report it (see below).
Your report is the check we are missing.

The reconciliation design searches Zammad tickets by tag with
`GET /api/v1/tickets/search?query=tags:"<trigger tag>"`. That query syntax and the two response
shapes it may return are unverified, and no reconciliation job runs yet.

## What does not work yet, and workarounds

- **Unassigned tickets fail in the default mode.** When the archive user mode is `owner` (the
  default) and the ticket has no owner, Chronikwerk does not archive it. The ticket gets the
  `pdf:error` tag and an internal note. *Workaround:* assign an owner, or set the ticket's
  archive user mode to `current_agent` or `fixed`, then apply the archive macro again.
- **Retrying a ticket in `current_agent` mode can fail.** A retry has no webhook to name the
  triggering agent, so Chronikwerk uses the ticket's last editor. If that is Chronikwerk's own
  account (for example because it wrote the earlier error note), the retry fails with a note
  instead of filing the archive under the wrong user. *Workaround:* apply the archive macro
  again in Zammad, or use `fixed` mode.
- **The requester is shown by login only.** The PDF shows the customer's Zammad login, which
  is usually but not always their email address.
- **Strict delivery-ID signing does not work with Zammad directly.** Zammad signs webhooks with
  HMAC-SHA1 over the body only. Leave `HARDENING__WEBHOOK__REQUIRE_DELIVERY_ID` unset unless a
  signing proxy sits in front of Chronikwerk.
- **The `.env.example` comment still says only `sha256=` signatures are accepted.** Zammad's
  native `sha1=` signature is accepted too. No change is needed on your side.
- **`202 Accepted` means recorded, not archived.** Requests are written to a journal before the
  reply and survive a restart. A request interrupted before its sealed files reached the store is
  captured again under a new revision ID. Back up the journal file; it cannot be rebuilt from the
  archive.
- **Storage layout changed.** Archives are now one directory per revision. Old
  `Ticket-<n>_<date>.pdf` pairs are left untouched and not imported.
- **A plain file share does not enforce retention.** Revisions written there record
  `retention_enforced` as false. Use such a share for testing only; production needs a share or
  store that enforces write-once retention itself.
- **SMB hard links, `flock` and read-only modes are untested.** The filesystem store links
  objects into place, locks each revision with `flock` and makes a published revision read-only
  with `chmod`. Test all three on your target share and mount options: a share that refuses
  `chmod` (for example CIFS without Unix extensions) leaves the revision published but its
  request never completes.
- **A success note can appear twice.** If Zammad stores the note but its reply is lost, the
  maintenance task posts the same "Archive revision complete" note again. The archive itself is
  not affected.
- **Validation material is not embedded.** Seal and timestamp validation runs offline against the
  trust anchors and does not check revocation. Certificates and revocation data are not stored in
  the package, and `signing.pades.level` `B-LT` does not embed validation information in the PDF.
- **Keep the archive fields agent-only in Zammad.** Whoever can edit the archive path and user
  fields decides which folder a ticket is archived to. The folder always stays inside the
  archive root.
- **Some endpoints are not protected.** The API documentation pages and the deep health check
  need no login, and the deep health check writes a test file to the archive. Keep the service
  on the default local-only address or behind a trusted proxy.
- **`LOG_LEVEL` overrides the configured log level.** If logging is quieter or louder than you
  configured, check whether a plain `LOG_LEVEL` variable is set in the environment, for example
  in an environment file shared with other services, and remove it.
- **Not built yet.** The S3 Object Lock store (`storage.backend=s3` has settings but no
  implementation); legal holds, expiry listing and disposal; the reconciliation and fixity jobs;
  the alerts webhook; mail evidence; evidence renewal timestamps; catalog commands in
  `chronikwerk-admin` (a rebuild method exists but has no command); and `verify --revision`. The
  settings for these exist but have no effect. A few building blocks are already in the code
  without a caller: the Zammad ticket search for reconciliation, and the catalog's export,
  verification-result and access-event records. The 15-minute maintenance task already repeats
  failed Zammad updates and removes abandoned staging and build directories.
- **The admin application is a preview.** It is optional for this alpha. Problems there are
  welcome reports but will not block the release.

## How to report a bug

Open an issue on GitHub with the **Bug report** template:
https://github.com/sebastianspicker/zammad-ticket-archiver/issues

Please include:

- the Chronikwerk version shown by `curl http://127.0.0.1:8080/healthz`, and the commit or tag
  you deployed;
- your Zammad version and how you run Chronikwerk (Docker Compose or systemd);
- what you did, what you expected (tags, note, PDF location), and what happened;
- the ticket's final tags and the text of the Chronikwerk internal note, including its
  `request_id` and `delivery_id`;
- the HTTP status Chronikwerk returned to Zammad for the webhook, if you can see it (for
  example in Zammad's log or a proxy log);
- a short log excerpt around the failure, for example from
  `docker compose logs --since 10m chronikwerk`;
- the output of `chronikwerk-admin dump-config`, which hides secrets.

Do **not** attach API tokens, webhook secrets, signing keys, archived PDFs, real ticket content
or personal data. Replace sensitive values with placeholders.

Report suspected security problems privately, as described in `SECURITY.md`, not in a public
issue.
