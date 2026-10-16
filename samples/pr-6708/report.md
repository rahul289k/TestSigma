# PR #6708: fix(common): preserve line breaks in documentation description editor

**Low risk.** This change modifies how line breaks are handled within the API documentation editor, potentially affecting text formatting in saved descriptions.

The update ensures that multiline text entered into the API documentation editor preserves its line breaks after saving and re-opening the field. Previously, the editor would incorrectly collapse these lines into a single block of text. This change applies to the markdown editor used for documenting collections.

## Retest these first


## Screens affected


## Product requirements


## Needs a human

- This change affects the documentation markdown editor found within collections, but we have not verified its appearance on the REST or GraphQL pages. Could you confirm which specific screens display this editor and if we should perform a manual retest on those pages?

## Changes we could not connect to a screen

- Collections › Documentation › Markdown Editor: can appear on the REST page, the GraphQL page, but the crawl never showed any of its controls
