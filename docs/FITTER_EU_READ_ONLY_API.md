# FITTER-EU Read-Only API

This document follows the FITTER-EU contribution section in the *FITTER-EU Data Schema Cookbook v0.1*. It describes the read-only listing endpoints and existing session/report endpoints. All require the application's existing login session; there is no separate API-key authentication scheme.

## 1.1 Project Overview

Dr Transition guides users through an analysis of how transition policies may create or worsen hazards, which population groups may be affected, and what mitigation measures could address those effects. The workflow brings policy, hazard, population, and mitigation information together for a selected place and economic sector. The read-only APIs documented here let an authenticated user retrieve the relevant reference data and analysis results, inspect their saved conversations, and download reports.

The conversation proceeds through these stages:

1. **Set the context.** The user selects a country, optionally narrows it to a region or national scope, and chooses a sector. These selections determine which policy, hazard, population, and mitigation records are relevant. Country, region, and sector listings provide the IDs and labels used by the other API requests.
2. **Review policies and identify hazards.** The user reviews policies available for the selected context and examines hazards that may be associated with, caused by, or worsened by those policies. Hazard ranking, salience, and effect-size information help describe and compare the hazards. These measures depend on the source data available for the selected context.
3. **Understand who may be affected.** The user reviews the population profiles associated with a hazard and the impacts described for those groups. Cached Eurostat data can add regional or national population percentages when a matching profile and valid cached value are available; it may not provide a percentage for every group or place.
4. **Review mitigation and its target groups.** The user considers measures associated with the hazard, how each measure is intended to work, and which affected groups it targets. The workflow supports checking the measure's fit and available evidence, confirming or clarifying the target groups, and completing evaluation or system-inquiry questions. A listed measure or target-group link is information for review; it does not by itself prove that the measure is effective.
5. **Save and communicate the analysis.** The conversation and its structured state are saved to a user-owned session. The user can return to that session, inspect its messages, or generate a PDF report for the current hazard, the user's hazard, or all hazards in the session.

The API follows the same stages: listing endpoints help establish context and retrieve policies, hazards, profiles, cached population data, and mitigation measures; analysis endpoints return hazard ranking and related calculations; session endpoints expose only the signed-in user's saved sessions; and the report endpoint returns a PDF for an owned session. All calls use the application's existing login session. The API is read-only: it provides access to the documented data and generated report but does not provide API operations for changing reference records or advancing the guided conversation.

## 1.2 Outcomes / Results Expected

| Project outcome | Description | Metrics / data | Notes |
| --- | --- | --- | --- |
| Review policies for the selected context | List policies relevant to the selected country, region or national scope, and sector, with objectives and source details where available. | Policy IDs, titles, jurisdiction, sector, objectives, and linked policy metadata. | Results depend on the policy records and source links available for that context. |
| Identify policy-linked twin-transition hazards | Show hazards associated with, caused, or worsened by policies connected to the green and digital transitions. | Hazard descriptions, policy links, salience, effect size, reach, and evidence references where available. | Ranking components depend on configured source data and cached population analysis. |
| Understand affected population groups | Identify the groups exposed to each hazard and summarize the impacts they experience. | Group labels, impact descriptions, supporting evidence, and regional/national population shares where available. | Population percentages are estimates and may be unavailable when no valid data or profile match exists. |
| Compare mitigation measures and their population targets | List measures addressing each hazard and explain which affected groups each measure targets and how the intended benefit reaches them. | Measure description, hazard link, target groups, targeting mechanism, evidence, and evaluation results where available. | Target-group links depend on the measure's documented or user-confirmed population information. |

## 1.3 Key Data Points

| S.No | Data point | Description | Data type / format | Notes |
| --- | --- | --- | --- | --- |
| D1 | Session metadata | Identifies a saved conversation and gives its title, selected country, region and sector, and creation or update times. | JSON | Supports reviewing the user's analysis in its selected context (outcomes: policy review and hazard identification). Geography and sector values may be IDs that correspond to D8. |
| D2 | Session state | The structured selections and analysis state saved for a policy conversation, including the context and confirmed findings represented in that session. | JSON object | Lets a user resume or inspect the analysis that connects the selected context to policies, hazards, affected groups, and mitigation (outcomes across section 1.2). Included in the session export. |
| D3 | Session messages | The conversation record, with user and assistant text, message status, and timestamps. | JSON array | Preserves how policies, hazards, affected groups, and mitigation choices were discussed or clarified (outcomes across section 1.2). Exported messages are ordered by creation time. |
| D4 | Mitigation report | A generated PDF summarizing mitigation analysis for a saved session and the requested hazard scope. | PDF (`application/pdf`) | Provides a shareable result of the mitigation outcome in section 1.2. Scope can be `current`, `user_hazard`, or `all_hazard`. |
| D5 | Ranked hazards | Hazard records and their ranking scores for the requested country, optional region, and sector. | JSON | Connects the selected context and policy landscape to the hazards users should review (outcome: identify policy-linked twin-transition hazards). Includes the formula and ranked hazard records. |
| D6 | Hazard salience | Salience values and their calculation inputs for a hazard in a geographic and sector context. | JSON | Indicates how prominently a hazard appears in the selected context, supporting comparison and prioritization (outcome: identify policy-linked twin-transition hazards). Filterable by country, sector, region, and hazard. |
| D7 | Hazard effect size | Estimated effect-size values and predictor calculation data for a hazard. | JSON | Helps describe the estimated strength of a hazard's relationship to relevant factors, adding context to hazard prioritization (outcome: identify policy-linked twin-transition hazards). `min_or` defaults to `1.0`. |
| D8 | Countries, regions, and sectors | Reference IDs and readable labels for the geographies and economic sectors used to scope policy and hazard queries. | JSON | Enables consumers to select and interpret a consistent context before reviewing policies and hazards (outcomes: review policies and identify hazards). Regions and sectors can be filtered by country. |
| D9 | Policies and hazards | Policy and hazard records, including standard/original policies and system, additional, or custom hazards. | JSON | Shows the policy context and the hazards linked to it, supporting the first two outcomes in section 1.2. Policies and custom hazards created by other users are omitted. |
| D10 | Socio-demographic profiles | Profile definitions describing population characteristics, including system profiles, additional-hazard profiles, and eligible session-specific profiles. | JSON | Supplies the group categories used to explain who is affected by each hazard (outcome: understand affected population groups). Session-specific data is owner-scoped. |
| D11 | Eurostat population data | Cached regional or national population percentages, dataset metadata, and related Eurostat lookup information. | JSON | Adds population context to affected-group analysis and helps quantify the groups discussed (outcome: understand affected population groups). Reads cache only; it does not call the external API. |
| D12 | Mitigation measures | Mitigation records with their descriptions, linked hazards, target population groups, and targeting information where available. | JSON | Shows which measures address a hazard, who each measure is intended to reach, and how the intended benefit relates to affected groups (outcome: compare mitigation measures and their population targets). Filterable by country, sector, or hazard. |

| Data point | Access method | Availability | Notes |
| --- | --- | --- | --- |
| D1–D3 | API | Yes | Requires a valid login session; session data is owner-scoped. |
| D4 | API / file response | Yes | Requires a valid login session; returns a PDF download. |
| D5–D7 | API | Yes | Requires a valid login session. |
| D8-D12 | API | Yes | Requires a valid login session; user-created records are scoped to their owner. |

## 1.4 Data Access / Availability

| Access method | Available | Notes |
| --- | --- | --- |
| API | Yes | Provides authenticated, read-only access to reference listings, policy and hazard analysis, affected-population profiles and data, mitigation measures, and the user's saved sessions. Use these endpoints to retrieve the evidence and records needed to review the outcomes in section 1.2. The OpenAPI schema at `/openapi.json` describes request parameters and response formats; Swagger UI at `/docs` lets an authenticated user inspect and call the available endpoints. On sync-only servers, the published schema contains only the permitted read-only routes and login. |
| File export | Yes | Downloads a session's generated PDF report, giving users a portable, readable account of the mitigation analysis and selected report scope. It supports communicating the mitigation outcome in section 1.2 beyond the interactive application. |
| Database access | No | Direct database connections or queries are not provided. Consumers should use the documented API to retrieve the approved records; this keeps access within application authentication, ownership checks, and response formats while supporting the policy, hazard, population, and mitigation outcomes in section 1.2. |
| Dashboard / UI | Yes | Provides the interactive, end-to-end workflow: choose a country, region and sector; review policies and hazards; examine affected groups; assess mitigation measures and their target groups; and save or export the analysis. It is the user-facing way to interpret and act on the same information exposed through the API. |
| Other | No | No separate API-key, partner, or alternate data-delivery interface is configured. API consumers authenticate through the application's existing login session, so access follows the same identity and ownership rules as the dashboard. |

## 1.5 API Structure

### Base URL and authentication

Use the deployed application origin as the base URL, for example `https://your-host.example`. Authenticate with the application's existing login flow and retain the `dr_transition_auth` session cookie. The login endpoint accepts form fields named `email` and `password`. Session endpoints only return sessions owned by the authenticated account.

```text
POST /login
Content-Type: application/x-www-form-urlencoded
Accept: application/json

email=<account email>&password=<account password>
```

### Read-only endpoints

| Method and path | Purpose | Query parameters | Response and access |
| --- | --- | --- | --- |
| `GET /api/sessions` | Lists the signed-in user's saved analyses so they can choose a conversation to review, export, or report on. Supports revisiting the policy-to-hazard-to-mitigation workflow in section 1.2. | None | JSON object with the authenticated user's session list. Logged-in user; owner-scoped. |
| `GET /api/sessions/{session_key}/export` | Retrieves one saved analysis as structured data so a client can inspect the selected context, saved state, and conversation history behind the analysis. | None | JSON session export with state and messages. Logged-in user; owner-scoped. |
| `GET /api/sessions/{session_key}/report` | Produces a shareable PDF summary of a saved analysis. Choose whether the report covers the current hazard, the user's hazard, or all hazards in that session. | `scope`: `current` (default), `user_hazard`, or `all_hazard` | PDF report. Logged-in user; owner-scoped. |
| `GET /api/hazards/ranked` | Ranks hazards for a selected country and sector, optionally narrowed to a region, so users can identify the most relevant policy-linked transition hazards for that context. | Required: `country_id`, `sector_id`; optional: `region_id` | JSON hazard ranking and component data. Logged-in user. |
| `GET /api/hazard-salience` | Retrieves salience scores and calculation details to show how prominent a hazard is in the selected geography and sector and support comparison of hazards. | Optional: `country`, `sector`, `region`, `hazard` | JSON salience and calculation data. Logged-in user. |
| `GET /api/hazard-effect-size` | Retrieves effect-size estimates and predictor details to help interpret the estimated magnitude of a hazard and the factors used in that estimate. | Optional: `sector`, `hazard`, `min_or` | JSON effect-size and predictor calculation data. Logged-in user. |
| `GET /api/knowledge/policies` | Lists policy records that an authorized administrator can choose when attaching documents to the main knowledge collection; it supports document curation, rather than end-user context browsing. | None | JSON policy choices available for document attachment. Admin user with main-knowledge permission. |
| `GET /api/v1/listings/countries` | Supplies the country catalogue so a client can begin setting the geographic context for policy, hazard, population, and mitigation queries. | `limit`, `offset` | Country IDs, names, and map codes. Logged-in user. |
| `GET /api/v1/listings/regions` | Lists regions, optionally for one country, so a client can narrow a national context to a subnational area before reviewing hazards and population impacts. | `country_id`, `limit`, `offset` | Regions with country context. Logged-in user. |
| `GET /api/v1/listings/sectors` | Lists available sectors, optionally filtered to a country, so policy and hazard results can be scoped to the economic activity being analyzed. | `country_id`, `limit`, `offset` | Sectors available globally or for a country. Logged-in user. |
| `GET /api/v1/listings/policies` | Returns the policies available for the selected country and sector, allowing users to review the policy context that may give rise to or worsen transition hazards. | `country_id`, `sector_id`, `limit`, `offset` | `policies` and `original_policies` arrays. Logged-in user; excludes policies created by other users. |
| `GET /api/v1/listings/hazards` | Lists hazards relevant to the selected country, sector, or region, including permitted additional hazards, so users can connect policy context to green and digital transition risks. | `country_id`, `sector_id`, `region_id`, `include_additional`, `limit`, `offset` | System, additional, and accessible custom hazards. Logged-in user; excludes custom hazards created by other users. |
| `GET /api/v1/listings/socio-demographics` | Retrieves population profile categories applicable to a context, hazard, or owned session so users can identify and describe groups affected by each hazard. | At least one of `country_id`, `region_id`, `sector_id`, `hazard_id`, `session_key`; `limit`, `offset` | System/additional profiles and, optionally, the user's session-specific profiles. `session_key` must belong to the logged-in user. |
| `GET /api/v1/listings/eurostat` | Reads cached population statistics for the requested context or profile to quantify affected groups and provide regional or national population shares where available. | At least one of `country_id`, `region_id`, `sector_id`, `hazard_id`, `profile`; `include_expired`, `limit`, `offset` | Cached Eurostat population values and data. Cache read only; expired rows are excluded by default. |
| `GET /api/v1/listings/mitigation-measures` | Lists measures applicable to a country, sector, or hazard, including target groups and links, so users can assess which measures address each hazard and how they are intended to benefit affected groups. | `country_id`, `sector_id`, `hazard_id`, `limit`, `offset` | Measures with target groups and hazard links. Logged-in user. |

All listing routes use HTTP GET and do not create or update records. `/api/v1/listings/eurostat` only reads cached rows. The existing `GET /api/hazards/ranked` may populate or refresh the Eurostat cache on a cache miss. `GET /api/sessions/{session_key}` is omitted because the UI restore route may repair persisted session state from messages.

Listing responses contain `items`, `limit`, and `offset`, except `/policies`, which returns separate `policies` and `original_policies` arrays. The default page size is 100 and the maximum is 500. IDs are database IDs and can be passed to related filters.

#### Listing filter behavior

| Endpoint | Filter behavior |
| --- | --- |
| `/countries` | Lists countries. |
| `/regions` | `country_id` filters regions by country. |
| `/sectors` | `country_id` filters to sectors assigned to that country. |
| `/policies` | `country_id` and `sector_id` independently filter both arrays. |
| `/hazards` | `sector_id` filters all hazard types; `country_id` filters country-scoped hazards; `region_id` filters custom hazards; `include_additional` defaults to `true`. |
| `/socio-demographics` | `country_id`, `sector_id`, and `hazard_id` filter catalogue profiles. `session_key` adds the owner's session profiles and supplies the session's country/region/sector filters when omitted. `region_id` is valid only with an owned `session_key`. At least one filter is required. |
| `/eurostat` | Filters cache rows by country, region, sector, hazard, and/or exact profile. At least one is required. `include_expired` defaults to `false`. |
| `/mitigation-measures` | Filters by country, sector, and/or system or additional hazard ID. |

### Important response fields

`GET /api/sessions` returns:

```json
{
  "sessions": [
    {
      "session_id": "<session-key>",
      "title": "<session title>",
      "country": "<country-id>",
      "region": "<region-id>",
      "sector": "<sector-id>",
      "updated_at": "<ISO-8601 timestamp>"
    }
  ]
}
```

`GET /api/sessions/{session_key}/export` returns `session` metadata and saved `data`, plus `messages`. Each message includes `role`, `content`, `raw_content`, `is_error`, and `created_at`. For non-admin users, `raw_content` is filtered to the same display-safe content as `content`.

`GET /api/hazards/ranked` returns the selected country, region, and sector, a ranking formula, and a `hazards` array. A missing country, region, or sector produces an error object with an empty hazards array.

## 1.6 Data Mapping

This section explains how fields returned by the API correspond to the application's stored records or generated service results. Use it to interpret identifiers, understand which saved data an export contains, and trace reference-listing records back to their source. The mappings also show how API responses support the outcomes in section 1.2: reviewing policies, identifying hazards, understanding affected groups, and comparing mitigation measures.

| API field | Application data | Meaning and purpose |
| --- | --- | --- |
| `session_id` | `user_sessions.session_key` | The API's session identifier is the session key stored for the saved conversation. Use it in the session export and report paths to retrieve the same user's saved analysis. |
| `country`, `region`, `sector` in session list | `user_sessions.country_id`, `region_id`, and `sector_id` | These values identify the context selected for that conversation. They are database IDs rather than display labels; resolve them through the country, region, and sector listings before presenting them to a user or using them as filters. |
| `session.data` in export | JSON stored in `user_sessions.session_data` | This is the structured state saved for the conversation, including the information used to preserve its analysis. It lets a client inspect the saved context and progress associated with policy, hazard, population-group, and mitigation outcomes. |
| `messages[]` | Rows in `user_chat_messages` for the owned session | Each item represents a user or assistant message belonging to that session, with its role, content, status, and timestamp. The messages provide the discussion and clarifications behind the structured session state; only messages for a session the user owns are returned. |
| `hazards[]` | Hazard ranking service output for the requested context | These are computed ranking results for the country, optional region, and sector supplied to the hazard-ranking endpoint. They are service output rather than a direct copy of one database table, and support identifying and comparing policy-linked hazards. |
| Report PDF | Generated from the owned session and selected report scope | The PDF is assembled when requested from the saved session and the chosen scope (`current`, `user_hazard`, or `all_hazard`). It presents the relevant mitigation analysis as a portable report; it is not a stored database field. |
| `/api/v1/listings/countries[].id` | `countries.id` | The country listing's `id` is the primary identifier for a country record. Pass it as `country_id` when filtering related regions, policies, hazards, profiles, population data, or mitigation measures. |
| `/api/v1/listings/regions[].id` | `regions.id`; parent is `regions.country_id` | A region `id` identifies the subnational area, while `country_id` identifies its parent country. The relationship lets clients display the region in the correct national context and use it to narrow supported queries. |
| `/api/v1/listings/sectors[].id` | `sectors.id`; country assignment is in `country_sectors` | A sector `id` identifies the economic activity used to scope analysis. The `country_sectors` relationship indicates which sectors are available for a country and helps clients build valid policy and hazard filters. |
| `/api/v1/listings/policies[].id` | `policies.id`; original rows use `original_policies.id` | Policy results can come from the standard policy catalogue or the original-policy collection, so the source collection determines which table owns the identifier. Use the returned policy record and source details to understand the policy context associated with hazards. |
| `/api/v1/listings/hazards[].id` | `system_hazards.id`, `additional_hazards.id`, or owner-visible `custom_hazards.id`; distinguished by `type` | Hazard identifiers belong to one of several hazard collections. Interpret each ID together with its `type`; this identifies the correct record and preserves the link between the selected hazard, its policy context, affected groups, and mitigation measures. |
| `/api/v1/listings/socio-demographics[].id` | System/additional profile ID or owner-scoped session profile ID | The identifier refers to a population profile from the shared catalogue or from the authenticated user's session. Use the profile type and owning session context to distinguish these sources when relating a profile to affected-group analysis. |
| `/api/v1/listings/eurostat[].id` | `eurostat_population_cache.id` | Each returned ID identifies a cached population-statistics record. The accompanying geography, profile, dataset, and percentage fields provide quantitative context for affected population groups; the listing reads this cache rather than fetching live Eurostat data. |
| `/api/v1/listings/mitigation-measures[].id` | `mitigation_measure_policies.id` | This ID identifies a mitigation-measure record and its policy association. Read it with the measure's hazard links, target groups, and targeting details to assess which affected groups a measure is intended to reach and how. |

## 1.7 API Example (How-To-Use)

The examples below follow the four outcomes in section 1.2. Each starts with a user goal and then shows the steps to make the request and interpret the result. They use `curl` and the application's existing login session. Replace the example host and ID placeholders with values from your deployment and the listing responses.

### Sign in once before using the examples

1. Set the application URL and log in. The cookie jar stores the login session so later requests can reuse it. Keep your credentials and cookie file private.

```bash
BASE_URL="https://your-host.example"

curl -sS -c cookies.txt \
  -H 'Accept: application/json' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "email=$DRTRANSITION_EMAIL" \
  --data-urlencode "password=$DRTRANSITION_PASSWORD" \
  "$BASE_URL/login"
```

### Example 1: Review policies for a selected context

**Use case:** A user wants to see which policies are available for a country and sector.

1. Find valid IDs with the country and sector listings. If the user needs a region-specific context, also retrieve regions for the selected country.
2. Request policies with the selected `country_id` and `sector_id`.
3. Review the returned `policies` and `original_policies` arrays for titles, jurisdictions, objectives, and source details.

```bash
curl -sS -b cookies.txt "$BASE_URL/api/v1/listings/countries"
curl -sS -b cookies.txt "$BASE_URL/api/v1/listings/sectors?country_id=<country-id>"
curl -sS -b cookies.txt "$BASE_URL/api/v1/listings/regions?country_id=<country-id>"
curl -sS -b cookies.txt \
  "$BASE_URL/api/v1/listings/policies?country_id=<country-id>&sector_id=<sector-id>"
```

### Example 2: Identify hazards for the selected context

**Use case:** A user wants to find and compare transition hazards for a country, sector, and optionally a region.

1. Reuse the `country_id`, `sector_id`, and optional `region_id` selected in Example 1.
2. Request ranked hazards to see the ranking and its component data.
3. If you also need the available hazard records, request the hazard listing using the same context. Compare the hazard records with the policy list to review the policy context.

```bash
curl -sS -b cookies.txt \
  "$BASE_URL/api/hazards/ranked?country_id=<country-id>&sector_id=<sector-id>&region_id=<region-id>"
curl -sS -b cookies.txt \
  "$BASE_URL/api/v1/listings/hazards?country_id=<country-id>&sector_id=<sector-id>&region_id=<region-id>"
```

Omit `region_id` when the analysis is national. The ranked response provides ranking information; the listing response provides hazard records. Check the returned hazard type and available evidence before interpreting a policy-hazard relationship.

### Example 3: Find affected population groups

**Use case:** A user wants to understand which groups are affected by a selected hazard and see population percentages when available.

1. Copy the relevant `hazard_id` from the hazard results and use the same country, sector, and optional region.
2. Request socio-demographic profiles to see the population groups available for that context and hazard.
3. Request Eurostat data with the same filters to read cached population values. The endpoint does not fetch new data from Eurostat.
4. Compare the profile labels and any returned percentages. Percentages may be absent if there is no matching cached value.

```bash
curl -sS -b cookies.txt \
  "$BASE_URL/api/v1/listings/socio-demographics?country_id=<country-id>&sector_id=<sector-id>&hazard_id=<hazard-id>"
curl -sS -b cookies.txt \
  "$BASE_URL/api/v1/listings/eurostat?country_id=<country-id>&sector_id=<sector-id>&hazard_id=<hazard-id>"
```

Add `region_id=<region-id>` to either request when a regional value is needed. To filter Eurostat data for a particular profile, use its profile value with the `profile` parameter as described in section 1.5.

### Example 4: Compare mitigation measures and their target groups

**Use case:** A user wants to find measures that address a hazard and understand which affected groups each measure is intended to reach.

1. Use the `country_id`, `sector_id`, and `hazard_id` for the analysis.
2. Request the mitigation-measure listing.
3. For each returned measure, review its hazard link, target groups, and targeting information. Compare these with the affected groups from Example 3 to assess whether the measure addresses their needs.

```bash
curl -sS -b cookies.txt \
  "$BASE_URL/api/v1/listings/mitigation-measures?country_id=<country-id>&sector_id=<sector-id>&hazard_id=<hazard-id>"
```

The measures and target-group links depend on the records available for the selected context. A matching hazard filter identifies measures associated with that hazard; it does not by itself establish that a measure is effective.

### Optional: Review or export a saved analysis

**Use case:** A user wants to reopen the conversation behind an analysis or share its report.

1. List the signed-in user's saved sessions and copy the required `session_id`.
2. Use that session key to retrieve the saved state and messages, or download a PDF report.
3. The session must belong to the signed-in user.

```bash
curl -sS -b cookies.txt "$BASE_URL/api/sessions"
curl -sS -b cookies.txt "$BASE_URL/api/sessions/<session-key>/export"
curl -fS -b cookies.txt \
  "$BASE_URL/api/sessions/<session-key>/report?scope=current" \
  -o mitigation-report.pdf
```

The API returns an authentication error if the login cookie is missing or invalid. Session exports and reports are owner-scoped.
