# Task 23 — First-time organizer onboarding

The onboarding is contextual rather than a modal walkthrough. It appears at
the two decisions where an explanation changes what the organizer expects,
without delaying returning organizers or participants.

## Entry choice

On a first visit, an inline guide above the two start cards distinguishes the
routes by the state of the orders:

- start a group link when drinks have not been collected yet;
- import a spreadsheet when the organizer already has a list.

It also says up front that both routes converge on an external checkout. The
guide remains in place while the organizer tries either route and is dismissed
only with **Got it**. **Show first-time guide** brings it back.

## Cart handoff

The cart explanation appears beside **Build the cart**, before the action is
taken. Three short steps set the boundary clearly: Boba Builder creates the
cart, Kung Fu Tea opens an editable copy, and the organizer reviews and pays
there. Building the cart or pressing **Got it** dismisses the hint; **How the
cart handoff works** reopens it.

## Persistence and fallback

`app/static/onboarding.js` stores independent `entry` and `handoff` dismissal
flags in `localStorage`. Clearing or reopening one does not affect the other.
If browser storage is unavailable or corrupt, the guide remains usable and
defaults to showing rather than failing the page.
