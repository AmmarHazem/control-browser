# Direct file downloads

Triage: `ready-for-agent`

## Problem Statement

A Task can find and navigate to a file, but neither an external agent using the Toolset nor an autonomous Run can save that file and return it to a caller. Browser Sessions currently reject downloads, so requests such as “find this song and download it” stop at a result page. A completed Run needs to return an actual retrievable file, not merely a source URL or a claim that a download happened.

## Solution

Add a Download capability shared by the MCP Toolset and REST Runs. An agent may supply a direct URL, use a direct URL found through search, or act on a download control in the current Session. The service saves the response bytes without conversion, records progress and provenance, and returns a Download ID, metadata, and a retrieval URL. A completed Download remains available for 24 hours. The first release supports direct file responses only; extracting media streams from YouTube or other watch pages is a later phase.

Search Tasks use configured SearXNG. The agent selects a convincing match based on the Task, tries another candidate when a download fails or hits an access barrier, and returns candidates rather than guessing when the match is uncertain.

## User Stories

### Finding and choosing a source

1. As a caller, I want to request a download by direct URL, so that I can save a file whose location I already know.
2. As an agent, I want to start a Download from a Ref on a page, so that a site's ordinary download button works.
3. As an agent, I want to Download a direct URL found in a search result or page, so that I can complete a discovery Task.
4. As a caller, I want to request any file type, so that the capability is not limited to audio and video.
5. As a caller, I want the Task's title, artist, year, edition, and other clues to guide source selection, so that the chosen file fits my request.
6. As a caller, I want an agent to decide autonomously when the match is clear, so that routine Tasks do not pause unnecessarily.
7. As a caller, I want plausible candidates returned when no match is convincing, so that the agent does not download a guess.
8. As a caller, I want the agent to try another candidate after a failed download, so that one broken source does not end the Task.
9. As a caller, I want a search Task to fail clearly when SearXNG is not configured, so that I know why discovery cannot start; a supplied URL must still work.
10. As a caller, I want a specific watch page that offers no direct file to be reported as unsupported, so that an apparent video player is not mistaken for a Download.

### Saving and returning files

11. As a caller, I want the exact source bytes and original format, so that the service never silently transcodes or changes the file.
12. As a caller, I want the actual filename, media type when known, byte count, source URL, and completion status, so that I can inspect the result before retrieving it.
13. As a caller, I want each requested file to have its own Download ID and status, so that a Task requesting several files can report partial success precisely.
14. As an MCP caller, I want a Session-scoped Action to start a Download and a way to check its status, so that my own agent can manage it.
15. As a REST caller, I want a Run to expose Download results in a structured, machine-readable field as well as its Trace, so that I do not need to parse prose for file IDs.
16. As a caller, I want a retrieval URL for a completed Download, so that I can transfer the bytes to my own application.
17. As a caller, I want the retrieval URL to work until expiry even if the browser Session or Run has closed, so that retrieval is independent of browser lifetime.
18. As a caller, I want repeated retrievals during the retention period, so that a network interruption on my side does not consume the Download.
19. As an operator, I want completed Downloads deleted after 24 hours, so that storage does not grow indefinitely.
20. As an operator, I want a configurable per-file cap defaulting to 5 GiB, so that one source cannot exhaust storage.

### Progress, cancellation, and failures

21. As a caller, I want bytes received and total bytes when known, so that I can display progress even when a source does not provide a length.
22. As a REST caller, I want Download progress and outcome in the Run Trace, so that I can observe and diagnose long Tasks.
23. As an MCP caller, I want to check status and cancel a Download, so that an external agent can stop unwanted transfers.
24. As a REST caller, I want aborting a Run to cancel its active Downloads, so that abort frees network and disk resources.
25. As a caller, I want partial files removed after failure, cancellation, or a size-limit breach, so that a failed Download cannot be retrieved as complete.
26. As a caller, I want failures to include the attempted source and a usable reason, so that I can choose a different path.
27. As a caller, I want a Run that exhausts reasonable candidates to end Failed with a concise list of attempts and barriers, so that I know what happened.
28. As a caller, I want login, payment, captcha, and DRM barriers identified without attempting to bypass them, so that the result is honest.

## Implementation Decisions

- A Download is a persisted transfer record separate from a Session and Run. It has an unguessable ID, source, suggested and stored filename, media type if available, byte counts, status (`pending`, `running`, `completed`, `failed`, `cancelled`, `expired`), timestamps, and failure reason. A Run records the IDs and metadata of its Downloads; a multi-file Task does not merge them into one archive.
- One Download service owns acquisition, progress, cancellation, retention, and retrieval. Both MCP Actions and Runs call the same interface; the Run loop does not use a private shortcut unavailable to the Toolset. A Ref-based Action uses the current Session to trigger the site's download. A URL-based Action uses the same Session's applicable browser state and network policy; a caller-supplied URL may be used without first navigating.
- A successful Download requires a finite direct response saved to disk. HTML error pages, redirects to a player, and unsupported streams must not be reported as completed files merely because bytes were received. Preserve source bytes; do not transcode, extract archives, execute files, or invoke a stream extractor.
- Search-based download Tasks require configured SearXNG. Ordinary browser navigation and the existing generic search behavior remain as they are; the download Task reports missing search configuration explicitly rather than pretending it found a source.
- The agent ranks candidates against the Task and may proceed without Confirmation when a match is convincing. If uncertain, return candidates without starting a speculative Download. After a candidate fails or hits an access barrier, try another reasonable candidate; if none succeeds, use Failed and include the attempts. An individual barrier remains visible in the Trace.
- Support multiple Downloads only when the Task requests multiple files. A Run's structured result lists completed and failed Downloads separately, including file IDs and retrieval URLs for completed ones. A Run is Completed only when its requested files succeed; explicit partial success is reported rather than described as full success.
- Retrieval uses an unguessable Download ID as a bearer capability: anyone who knows it can retrieve the file until expiry without an API key or tenant check. Other management and status operations retain the service's existing API authentication. Treat IDs and retrieval URLs as sensitive: do not put them in routine logs or page referrers. Serve bytes as an attachment with a safe filename and no shared caching. This intentionally differs from the current tenant-scoped record model and should be revisited before exposing the service to untrusted users.
- Stream to temporary storage, enforce the configurable 5 GiB default cap even when `Content-Length` is missing or false, and atomically publish only complete files. Reject or cancel transfers when limits are reached; clean up partial files. Expire files after 24 hours, including across process restarts. Bound concurrent transfers and overall disk use with operator settings so several maximum-size Downloads cannot exhaust the host.
- Apply the existing domain and private-address policy to every acquisition request and redirect, including DNS resolution, not just the initial page navigation. The destination path and filename are generated or sanitized by the service; a response cannot choose a filesystem path. Downloaded bytes are never executed or unpacked.
- Expose Download status and cancellation through both interfaces, and retrieval through REST. A Session closing does not remove an already completed Download. Aborting a Run cancels its active transfers; completion and cancellation must be race-safe.

## Testing Decisions

- Test observable behavior through the existing real-ASGI, real-Chromium fixture-site integration seam and fake model endpoint. Extend the fixture site with direct responses, a download button, redirects, delayed streaming, a missing length, oversized content, and error pages. Assert the retrieved bytes, metadata, statuses, Trace events, and cleanup rather than private method calls.
- Exercise both an MCP-driven Session and an autonomous Run against the same fixture behavior. Cover direct URL, Ref-triggered download, search candidate choice with a controlled search response, multi-file and partial outcomes, a wrong match, and missing SearXNG.
- Test cancellation, Run abort, expiry/restart, concurrent limits, public-by-ID retrieval, and blocked redirects. Existing Session Action, Run, and network-guard integration tests provide the prior pattern. Focused unit tests may cover filename sanitization, size accounting, and expiry calculations where the integration seam is unnecessarily slow.
- Do not make live third-party sites or a real model a required test dependency. Add optional real-web evals after implementation to measure candidate selection; a test must not treat a source URL or homepage price as proof of a successful Download.

## Out of Scope

- YouTube and other watch-page media extraction, HLS/DASH assembly, transcoding, and format conversion. A YouTube URL without a direct downloadable file will not fulfill the first release.
- Bypassing logins, payment, captchas, DRM, or anti-bot controls; per-user browser profiles and authenticated-site support.
- Verifying copyright ownership or licensing of arbitrary sources. The service does not bypass access barriers; callers remain responsible for what they ask it to retrieve.
- Cloud object storage, cross-replica coordination, permanent archives, and malware scanning. Files remain opaque and are served as attachments; deployment operators must account for local disk capacity.

## Further Notes

The existing v1 spec and README explicitly exclude downloads; update that documentation when the capability ships. The first release should be judged by a caller successfully retrieving matching bytes, not by the agent finding a page that mentions the requested title.