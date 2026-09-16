# Task 17 — QR Code for Group Order Link

The organizer dashboard shows a QR code in the participant-link card as soon
as organizer access succeeds. It encodes the same absolute public URL used by
**Copy link** and never contains the private organizer token.

The compact code can be scanned directly or opened as a full-screen room view.
That view keeps the group title, selected store, exact local deadline, and URL
next to the large code so people can confirm what they are joining. The details
continue to update if the organizer extends the deadline or closes the order.

**Download QR** creates a 1200 × 1500 PNG invitation with the same title, store,
deadline, public URL, and a crisp four-module quiet zone. Generation happens
entirely in the browser; participant links are not sent to a QR-code service.
The QR encoder is bundled under its MIT license so display and download also
work without an internet connection.

The encoder uses medium error correction to keep typical group links easy to
scan at room distance without making the pattern unnecessarily dense. The
bundled build supports URLs up to 213 UTF-8 bytes; if a deployment has an
unusually long origin beyond that limit, the dashboard leaves Copy link and
Open participant view available and explains that QR generation is unavailable.
