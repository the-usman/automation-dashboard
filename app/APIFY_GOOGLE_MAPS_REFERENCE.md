# Apify Google Maps Scraper — Input & Output Reference

Actor: **`compass/crawler-google-places`** (API id `compass~crawler-google-places`,
internal id `nwua9Gu5YrADL7ZDj`). Pulled from the latest build's input schema +
dataset output schema (2026-10-06 build `0.14.763`).

> Fields marked **`($)`** incur an extra per-use charge on top of the base
> per-place price. No input field is strictly *required*, but you need **one of**
> `searchStringsArray`, `startUrls`, `placeIds`, or `allPlacesNoSearchAction` for the
> run to find anything. `locationQuery` — **one location per run**.

---

## INPUT (39 fields)

### Search & location (the core filters)
| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `searchStringsArray` | string[] | — | Search terms, e.g. `["English breakfast","pet shelter"]`. Primary input. |
| `locationQuery` | string | — | Free-text location, e.g. `"Austin, Texas, USA"`. **One per run.** Simpler = better (City + Country). |
| `maxCrawledPlacesPerSearch` | integer | — | Results per search term/URL. Higher = slower/costlier. **Always cap.** |
| `language` | select | `en` | `en, af, az, id, ms, bs, ca, cs, da, de, et, es, es-419, eu, fil, fr, …` |
| `countryCode` | select | `""` | ISO-2 (`us`, `gb`, …). Geo narrowing. |
| `city` | string | — | City only (no state/country). |
| `state` | string | — | State/province (mainly US). |
| `county` | string | — | County/administrative area. |
| `postalCode` | string | — | Combine with **Country only**, never City. One at a time. |
| `customGeolocation` | object(json) | — | GeoJSON polygon/point; `[longitude, latitude]` order. |

### Place-level filters
| Key | Type | Default | Enum / notes |
|-----|------|---------|--------------|
| `categoryFilterWords` `($)` | select[] | — | Restrict to Google categories (flat fee for the field). |
| `searchMatching` `($)` | select | `all` | `all` \| `only_includes` \| `only_exact` — match the Search term against place name. |
| `placeMinimumStars` `($)` | select | `""` | `""` \| `two` \| `twoAndHalf` \| `three` \| `threeAndHalf` \| `four` \| `fourAndHalf` |
| `website` `($)` | select | `allPlaces` | `allPlaces` \| `withWebsite` \| `withoutWebsite` |
| `skipClosedPlaces` `($)` | boolean | `false` | Skip temp/permanently closed. |

### Add-ons / enrichment (all `($)`)
| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `scrapeContacts` | boolean | `false` | **Enrich with website emails + socials.** ← what gives you lead emails. |
| `scrapeSocialMediaProfiles` | object | `{facebooks,instagrams,youtubes,tiktoks,twitters: false}` | Deep public data per social profile. |
| `maximumLeadsEnrichmentRecords` | integer | `0` | Extract employee leads per place (names, titles, emails, LinkedIn). 0–999. |
| `leadsEnrichmentDepartments` | select[] | — | Limit leads to depts (Sales, Marketing, C-Suite…). Needs the leads add-on. |
| `verifyLeadsEnrichmentEmails` | boolean | `false` | Verify each enriched lead email → `emailVerification`. |
| `scrapePlaceDetailPage` | boolean | `false` | Open each place's detail page (slower, richer). |
| `scrapeTableReservationProvider` | boolean | `false` | Restaurant reservation provider data. |
| `scrapeOrderOnline` | boolean | `false` | Restaurant pickup/delivery providers. |
| `includeWebResults` | boolean | `false` | "Web results" section. |
| `scrapeDirectories` | boolean | `false` | Businesses inside malls/centers. |
| `maxQuestions` | integer | `0` | Q&A per place (0 = first only). |
| `enableCompetitorAnalysis` | boolean | `false` | AI competitor report → separate dataset. |
| `maxCompetitorsToAnalyze` | integer | `30` | Cap for the above (≤100). |

### Reviews & images
| Key | Type | Default | Enum / notes |
|-----|------|---------|--------------|
| `maxReviews` `($)` | integer | `0` | Reviews per place. |
| `reviewsStartDate` | string(date) | — | Absolute `2024-05-03` or relative `8 days` / `3 months`. |
| `reviewsSort` | select | `newest` | `newest` \| `mostRelevant` \| `highestRanking` \| `lowestRanking` |
| `reviewsFilterString` | string | `""` | Only reviews containing keywords (≤500 bytes). |
| `reviewsOrigin` | select | `all` | `all` \| `google` |
| `scrapeReviewsPersonalData` | boolean | `true` | Reviewer id/name/url/photo. |
| `maxImages` `($)` | integer | `0` | Images per place. |
| `scrapeImageAuthors` | boolean | `false` | Image author names (slower). |

### Direct targeting (instead of search)
| Key | Type | Notes |
|-----|------|-------|
| `startUrls` | requestList | `https://google.com/maps/…` URLs (max 300 results/URL). |
| `placeIds` | string[] | Place IDs like `ChIJ…`. |
| `allPlacesNoSearchAction` | select | `""` \| `all_places_no_search_ocr` \| `all_places_no_search_mouse` — scrape everything on the current map view. |

---

## OUTPUT (dataset item — 100 fields)

### Identity & classification
`title`, `subTitle`, `placeId`, `cid`, `fid`, `kgmid`, `businessProfileId`,
`categoryName`, `categories[]`, `description`, `isAdvertisement`, `rank`,
`searchString`, `searchPageUrl`, `scrapedAt`, `language`, `url` (Google Maps URL).

### Location & address
`address`, `street`, `neighborhood`, `city`, `state`, `countryCode`, `postalCode`,
`location` (lat/lng), `plusCode`, `locatedIn`, `floor`, `parentPlaceUrl`.

### Contact & web (populated by `scrapeContacts`)
`website`, `phone`, `phoneUnformatted`, `phones[]`, `phonesUncertain[]`,
`emails[]`, `linkedIns[]`, `twitters[]`, `instagrams[]`, `facebooks[]`,
`youtubes[]`, `tiktoks[]`, `pinterests[]`, `discords[]`, `impressum`.
Deep social objects: `facebookProfiles[]`, `instagramProfiles[]`,
`youtubeProfiles[]`, `tiktokProfiles[]`, `twitterProfiles[]`.

### Ratings & reviews
`totalScore`, `reviewsCount`, `reviewsDistribution`, `reviewsTags[]`,
`reviewsRemovedNotice`, `placesTags[]`, `questionsAndAnswers`, `ownerUpdates[]`,
`updatesFromCustomers`, `peopleAlsoSearch[]`.

### Hours & status
`openingHours`, `additionalOpeningHours`, `openingHoursBusinessConfirmationText`,
`permanentlyClosed`, `temporarilyClosed`, `wasOpenAtScrapeTime`,
`popularTimesLiveText`, `popularTimesLivePercent`, `popularTimesHistogram`,
`claimThisBusiness`.

### Media & misc
`imageUrl`, `imagesCount`, `imageCategories[]`, `price`, `additionalInfo`,
`gasPrices[]`, `webResults[]`, `userPlaceNote`.

### Leads enrichment (populated by `maximumLeadsEnrichmentRecords`)
`leadsEnrichment[]` — employee name, job title, email, phone, LinkedIn, company data.

### Restaurant / ordering
`restaurantData`, `menu`, `googleFoodUrl`, `reserveTableUrl`,
`tableReservationLinks[]`, `tableReservationProviders[]`, `orderOnline`,
`bookingLinks[]`, `servicesLink`, `ownerDescription`.

### Hotel-specific
`hotelStars`, `hotelDescription`, `checkInDate`, `checkOutDate`,
`similarHotelsNearby[]`, `hotelReviewSummary`, `hotelAds[]`.

### External-service places
`isExternalServicePlace`, `externalServiceProvider`, `externalId`,
`inputPlaceId`, `inputStartUrl`.

---

## What our backend sends & keeps

`leadgen/schemas.py → GoogleMapsFilters` currently sends: `searchStringsArray`,
`locationQuery`, `maxCrawledPlacesPerSearch`, `language`, `scrapeContacts`,
`categoryFilterWords`, `countryCode`, `city`, `state`, `postalCode`. (All valid keys
above; empty ones are dropped before the run by `actors.run_google_maps`.)

`leadgen/actors.py → normalize_place` keeps: `title→name`, `categoryName→category`,
`address`, `city`, `state`, `countryCode→country`, `postalCode`, `phone`, `website`,
`domain`, `emails`, `totalScore→rating`, `reviewsCount→reviews_count`, `placeId`, `url`.
All of these are real output field names above.

**Tips**
- For lead emails you must keep `scrapeContacts: true` (it's the `($)` add-on that
  populates `emails`, `linkedIns`, `instagrams`, …).
- `locationQuery` empty → broad/zero results. Always set a location.
- To pull per-company *people* (names + titles + emails), set
  `maximumLeadsEnrichmentRecords` (+ optional `leadsEnrichmentDepartments`,
  `verifyLeadsEnrichmentEmails`) and read `leadsEnrichment[]` — not currently sent by
  our generator; add it to `GoogleMapsFilters` if you want employee-level leads.
