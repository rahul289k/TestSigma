# PR #6706: feat(common): support comments in saved response examples

**Medium risk.** This update introduces support for comments in saved API response examples, which may impact how response bodies are parsed and formatted.

The application now supports JSON-with-comments (JSONC) in saved REST and GraphQL response examples, allowing users to include comments that persist through formatting, saving, and downloading. A new prettify button has been added to editable response bodies, and the system now validates JSONC input to prevent malformed data from being silently altered.

## Retest these first

- **[Medium] Filter history items in the history section** - Changes to how response data is processed and validated could inadvertently affect the filtering logic or data handling within the history section.
  - Open the REST page → click **Send** → check **Filter**

## Screens affected

- [Medium] REST response view: Filter, More, Wrap lines
- [Medium] Response options menu: Clear Response ⌘ Delete, Filter, Generate Data Schema, More, Wrap lines
- [Medium] Data schema generator: Filter, More, Wrap lines
- [Medium] Schema language selector: Filter, More, Wrap lines
- [Medium] C# schema view: Filter, More, Wrap lines
- [Medium] Schema language selector popover: Filter, More, Wrap lines

## Product requirements

- **At risk**: User can filter history items using the Filter button in the history section. - The history filtering functionality must be verified to ensure that the underlying changes to response data handling do not impact the ability to search and filter past requests.
  > "You can filter your history by clicking on the "Filter" button in the history section" (https://docs.hoppscotch.io/documentation/features/history#filter-history)

## Needs a human

- The GraphQL example response area was modified, but we have not identified the specific screen where this is accessed; could you clarify where this appears and if it requires dedicated testing?

## Changes we could not connect to a screen

- Gql › Example › Response: can appear on the REST page, but the crawl never showed any of its controls
- outside the web app package
