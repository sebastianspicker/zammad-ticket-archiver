# Zammad contract fixtures

Response and payload shapes the service must accept from a real Zammad. They follow the
official API documentation (docs.zammad.org: ticket show with `?expand=true`, tags,
articles) and the webhook payload page (admin-docs.zammad.org). They are modelled on
those documents, not recorded from a live instance; replace them with recorded bodies
after the first live verification run.

- `ticket_show_expand.json`: `GET /api/v1/tickets/{id}?expand=true`. Relations are
  integer `*_id` keys plus, because of `expand`, login strings (`owner`, `updated_by`,
  `customer`). Custom object attributes such as `archive_path` are top-level keys;
  `preferences` carries no custom fields.
- `webhook_trigger_payload.json`: the default body of a trigger webhook. Top-level keys are
  `ticket` and `article` only (no `user`); user relations are nested objects with `login`,
  and custom attributes sit on `ticket`. Zammad signs it as `X-Hub-Signature: sha1=<hex>`.
- `ticket_articles_by_ticket.json`: `GET /api/v1/ticket_articles/by_ticket/{id}`. Attachment
  `size` is a string and the MIME type lives in `preferences["Content-Type"]`.
- `tags.json`: `GET /api/v1/tags?object=Ticket&o_id={id}`, an object wrapping `tags`.
