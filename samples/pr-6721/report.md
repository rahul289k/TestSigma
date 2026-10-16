# PR #6721: fix(common): do not add filename to text fields in multipart requests

**Medium risk.** This update changes how text fields with custom content types are formatted in multipart API requests to prevent incorrect file naming.

We have updated the way multipart requests are processed so that text fields with manually assigned content types are no longer incorrectly labeled as files. Previously, these fields were being sent with a filename attribute, which could cause issues with certain servers. This change ensures they are treated as standard text parts while still respecting the user-defined content type.

## Retest these first

- **[Low] View the GraphQL schema explorer** - The schema explorer might be affected if it relies on the underlying request serialization logic.
  - Open the REST page → click **Switch protocol** → click **GraphQL** → check **Switch protocol**
- **[Low] Access API request collections** - Saved requests in collections might now send multipart data differently than before.
  - Open the REST page → check **Collections**
- **[Low] Access previous requests via the history panel** - History items may contain multipart requests that need to be verified against the new serialization format.
  - Open the REST page → check **History**
- **[Low] Copy generated code snippet to clipboard** - Generated code snippets for multipart requests might change, potentially affecting how users copy and use them.
  - Open the REST page → click **Generate Code** → check **Generate Code**
- **[Low] Create and manage API environments** - Environment variables used within multipart request fields might interact differently with the updated serialization.
  - Open the REST page → check **Environments**
- **[Low] Filter history items in the history section** - Filtering history items could be impacted if the underlying request data structure has changed.
  - Open the REST page → click **Send** → check **Send**
- **[Low] Generate code snippets for API requests** - Code generation for multipart requests needs verification to ensure the output remains accurate.
  - Open the REST page → check **Generate Code**
- **[Low] Save a response as an example** - Saving a response as an example might be affected if the request structure is altered.
  - Open the REST page → click **Send** → check **Send**
- **[Low] Send a request and save to history** - Sending a request and saving it to history is the primary path for verifying that multipart data is handled correctly.
  - Open the REST page → check **Send**

## Screens affected

- [Medium] Generate code view: Shell - cURL, Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Medium] Code generation language selector: C - cURL, PHP - cURL, Shell - cURL, Shell - cURL, Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command
- [Medium] Import cURL dialog: cURL, Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Medium] Code language selector popover: C - cURL, PHP - cURL, Shell - cURL, Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments
- [Medium] Clojure language selector popover: C - cURL, PHP - cURL, Shell - cURL, Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments
- [Low] REST request - Main view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Command palette: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Bulk edit parameters: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Environments management view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Expanded sidebar view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Collapsed sidebar view: Switch protocol, Enter a URL or paste a cURL command, Send
- [Low] Environment selector: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Request variables view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Protocol switcher: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Collections dialog: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] Request options menu: Mock Servers, Show code S, Switch protocol, Clear all ⌫, Collections, Enter a URL or paste a cURL command, Environments, Generate Code
- [Low] Hoppscotch account menu: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Save request dialog: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Pre-request script editor: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Environment quick peek: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Interceptor settings: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Request body configuration: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Authorization configuration: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Shared requests view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] New request view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] REST response view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Post-request script editor: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] History view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Edit environment dialog: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Proxy settings: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Postman collection import dialog: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] GraphQL request view: Mock Servers, Request, Switch protocol, Collections, Connect, Environments, History, Schema
- [Low] Import from OpenAPI menu: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] Login prompt for AI naming: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] History filter menu: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Import collections dialog: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] Header override settings: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Auth method selector: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Login for AI script modification: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Response options menu: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Export collections menu: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] Add new environment variable: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Selected code language display: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Data schema generator: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Import Postman collection: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] GraphQL schema view: Mock Servers, Request, Switch protocol, Collections, Connect, Environments, History, Schema
- [Low] Header toggle view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Import OpenAPI from file: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] AWS Signature configuration: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] AWS Auth method selector: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Postman import confirmation: Mock Servers, Switch protocol, Collections, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History
- [Low] Schema language selector: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Basic Auth configuration: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] C# schema view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Clojure code snippet view: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Schema language selector popover: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send
- [Low] Basic Auth method selector: Mock Servers, Switch protocol, Collections, Enter a URL or paste a cURL command, Environments, Generate Code, History, Send

## Product requirements

- **Weak signal**: The AI Assistant is an experimental chat assistant that can act on requests and collections. - AI assistant interactions involving multipart requests should be monitored.
  > "The AI Assistant is an experimental chat assistant that can act on the request, response, environment, and collections you're working with." (https://docs.hoppscotch.io/documentation/features/ai-features#ai-assistant)
- **Weak signal**: User can organize collections and create sub-collections via drag and drop. - Ensure drag and drop organization does not corrupt multipart request data.
  > "You can organize your collections by dragging and dropping them to the desired location." (https://docs.hoppscotch.io/documentation/features/collections#organizing-collections)
- **Weak signal**: User can organize API requests using collections and access them via the side panel. - Verify that requests stored in collections maintain their multipart settings.
  > "You can access the collections by clicking the "Collections" icon on the side panel." (https://docs.hoppscotch.io/documentation/features/collections)
- **Weak signal**: User can create environments by clicking the Environments icon and reference variables using double angle brackets. - Confirm that environment variables are correctly resolved in multipart fields.
  > "Click on the ”Environments” icon on the sidebar to create environments." (https://docs.hoppscotch.io/documentation/features/environments#creating-an-environment)
- **Weak signal**: User can group variable data into environments to reference throughout requests and scripts. - Ensure grouped variable data is correctly passed to multipart requests.
  > "An environment allows you to group together a set of variable data." (https://docs.hoppscotch.io/documentation/features/environments)
- **Weak signal**: User can duplicate an existing environment from the More menu option. - Verify that duplicated environments retain correct multipart request configurations.
  > "From the dropdown menu, select Duplicate. A new environment with the suffix - Duplicate will be created." (https://docs.hoppscotch.io/documentation/features/environments#duplicating-an-environment)
- **Weak signal**: User can explore the data structure using the GraphQL schema explorer. - Check if GraphQL schema exploration is affected by the underlying serialization changes.
  > "GraphQL uses a schema to define the structure of the data, the schema explorer helps you" (https://docs.hoppscotch.io/documentation/features/graphql-api-testing#schema)
- **Weak signal**: User can access previous requests by clicking on the desired entry in the history section. - Ensure history entries correctly represent the multipart request structure.
  > "click on the desired request in the history section." (https://docs.hoppscotch.io/documentation/features/history#accessing-requests-from-history)
- **Weak signal**: Hoppscotch automatically saves requests to history when clicking the Send button. - Verify that requests are saved to history with the correct multipart formatting.
  > "click on the "Send" button on the request page. Hoppscotch will automatically save the request" (https://docs.hoppscotch.io/documentation/features/history#save-requests-to-history)
- **Weak signal**: User can access the request and response history by clicking the History icon on the side panel. - Confirm that accessing history does not alter multipart request data.
  > "You can access the history of your requests by clicking on the "History" icon on the side panel." (https://docs.hoppscotch.io/documentation/features/history)
- **Weak signal**: User can generate code snippets by clicking the Code Generate icon in the right sidebar. - Ensure generated code snippets accurately reflect the multipart request structure.
  > "locate the Code Generate <> icon in the right sidebar, alongside collections, environments, and history." (https://docs.hoppscotch.io/documentation/features/snippets#generate-code-snippets)

## Needs a human

- Where is the request preview feature located in the UI, and should we perform a retest on it?
- Where can a user find the documentation explorer, and does it require testing for this change?
- Where is the response view displayed, and should we verify it for this update?
- Where is the response meta information shown, and does it need to be retested?
- Where is the schema view located, and should we include it in our testing?
- Where can a user access the schema search, and does it need a retest?
- Where is the subscription log visible, and should we test it for this change?
- Where is the main index page used, and does it require a retest?

## Changes we could not connect to a screen

- Utils › Effective URL: shared logic that no screen we visited uses (within 3 steps)
- Collections › Documentation › Request Preview: can appear on the REST page, the GraphQL page, but the crawl never showed any of its controls
- Embeds › GQLIndex: appears on the e :id page, which this crawl did not visit
- Embeds › GQLRequest: appears on the e :id page, which this crawl did not visit
- Embeds › Request: appears on the e :id page, which this crawl did not visit
- Gql › Doc Explorer: can appear on the REST page, but the crawl never showed any of its controls
- Gql › Response: can appear on the REST page, but the crawl never showed any of its controls
- Gql › Response Meta: can appear on the REST page, but the crawl never showed any of its controls
- Gql › Schema: can appear on the REST page, but the crawl never showed any of its controls
- Gql › Schema Search: can appear on the REST page, but the crawl never showed any of its controls
- Gql › Subscription Log: can appear on the REST page, but the crawl never showed any of its controls
- src/pages/index.vue: can appear on the REST page, but the crawl never showed any of its controls
- CLI (not part of the web UI)
- CLI (not part of the web UI)
- CLI (not part of the web UI)
- CLI (not part of the web UI)
- desktop app shell (not part of the web UI)
- desktop app shell (not part of the web UI)
- platform kernel library
