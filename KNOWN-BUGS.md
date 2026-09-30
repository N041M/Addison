# Known bugs

This file holds defects with a known wrong behaviour and a way to see it. Each
entry names a repro and the code area. Strike an entry only after its own check
has been re-run against a build that contains the fix, and write what the re-run
showed. Design questions go in [`docs/KNOWN-GAPS.md`](docs/KNOWN-GAPS.md).

Two passes feed this file. The bug hunt of 2026-09-29 comes first because it is
the newer pass and most of its entries are still open. The whole-app test pass of
8–9 August follows it, and all of its entries are struck.

## Bug hunt, 2026-09-29

**What ran.** The hunt ran against commit `21ff450` on master, with a clean tree.
Ten read-only hunters each took one area: the permission gate and policy, the
tools, providers and tool servers, storage/snapshots/routines/channels, the RPC
boundary, the Rust shell, frontend state, frontend components, cost, and test and
document honesty. Every gate was green before the hunt started: Python 2,183
passed and 1 skipped, the frontend 1,032 passed, and Rust 201 passed with clippy
clean. The Rust gate only ran with `DEVELOPER_DIR=/Library/Developer/CommandLineTools`,
because the Xcode licence has not been accepted on this machine and `cc` exits 69
without it.

The coordinator also drove the real core over stdio against a fake
OpenAI-compatible server on 127.0.0.1, with the database in a temporary directory.
That covered a plain turn, a tool turn, save/undo/redo/undo, a refused save
followed by a clean turn, Stop while a card was waiting, rewind with tool pairs
intact, and a kill-and-restart on the same database, and each of those behaved.
The entries marked **reproduced by the coordinator** were run again from scratch
by the coordinator rather than taken from a hunter's report.

**What did not run.** Nothing here was checked against a real provider, a real
Telegram bot, real launchd, or Windows. The desktop app itself was not launched,
so nothing below was seen in the real webview. Frontend findings come from
rendering the real components in jsdom, and a few layout findings come from
Chromium through Vite. Findings that depend on a vendor's request schema (18, 19
and 55) reproduce the exact bytes Addison sends; the refusal itself comes from the
vendor's published schema and was not observed live.

Numbering continues from the August pass, so an entry keeps its number when
others are struck.

### P1 — broken features

16. **Restore points saved before an update cannot be restored after it,
    including the permanent first restore point.** Save restore points with the
    build from just before messaging channels (`git archive 3497faf^`), then open
    the same database with the current build. Every row is still listed. The
    one-action restore answers "Addison couldn't read the setups it saved for
    you", and restoring any row by id answers "That restore point can't be
    read". The cause is that `_decode_payload` rejects a payload with any captured
    table missing, and three tables were added after restore points shipped
    (`mcp_servers` on 08-06, `automations` on 08-07, `channels` on 08-22). Adding
    `"channels": []` to an old payload makes it decode. Every install upgraded
    across those dates has lost all of its rollback history, and the next captured
    table will do it again. The docstring already tolerates missing columns for
    this exact reason. `tests/test_snapshots.py` has a test that asserts the
    strict behaviour. **Reproduced by the coordinator.**
    `agent_core/snapshots/snapshot_manager.py` (`_decode_payload`) ·
    `agent_core/snapshots/scope.py`

17. **A turn fails if the person takes more than two minutes to answer a
    permission card.** The tool still runs. The chat then shows "Addison couldn't
    reach a model to answer just now" and the model is never asked again.
    Live repro: the fake model asks for `save_file` and the card is answered Allow
    after 125 s. The file is on disk, the model received one request, and
    `sendMessage` returned that error. The routed path starts its 120-second
    fallback budget once per turn (`turn_started`), so card waits, tool run time
    and typing an arming code are all charged to it. Production always takes the
    routed path. `rpc/conversation.py` then removes the partial exchange, so the
    action happened and the transcript does not show it. The arming card is the
    worst case, because a job can be armed behind an error message.
    **Reproduced by the coordinator.**
    `agent_core/orchestrator.py` (`_run_with_fallback`, `_FALLBACK_BUDGET_SECONDS`)

18. **Once a tool server is checked, every Developer message fails on Anthropic and
    OpenAI.** Tool ids are `mcp:<server name>:<tool>`, and all three cloud
    adapters send the id verbatim as the tool's name. Anthropic and OpenAI only
    accept `^[a-zA-Z0-9_-]{1,64}$`. A colon and a space are both outside that. The
    tool list goes out on every request, so each message is refused with "The
    request to … failed (status 400)" and the router does not fall forward on a
    rejected request. Gemini accepts the colon and refuses the space. Running a
    server named "github" and one named "My Files" through `McpCatalog` and the
    adapters' `_translate_tools` gives `mcp:github:list_issues` and
    `mcp:My Files:search`, and neither matches. No test sends an MCP tool through
    an adapter. **Reproduced by the coordinator.**
    `agent_core/mcp_catalog.py` (`mcp_tool_id`) ·
    `agent_core/providers/{anthropic,openai,google}_provider.py` (`_translate_tools`)

19. **GPT-5, GPT-5 mini, o3 and o4-mini fail every message.** The OpenAI adapter
    always sends `max_tokens: 4096`. OpenAI refuses `max_tokens` on its reasoning
    models and asks for `max_completion_tokens`, which appears nowhere in the
    tree. `gpt-5` is the curated default for an OpenAI key. GPT-4.1 and GPT-4o
    are unaffected.
    `agent_core/providers/openai_provider.py` (`send`)

20. **"Run a model on this computer" never finishes on screen.** The window shows
    the model as ready the moment the download starts, then shows "setting up…"
    for ever. A failed download is never shown as an error, every Set up button
    stays disabled, and the finished model does not appear in the picker until
    the app restarts. `model.startLocalSetup` answers `{ok, started}` at once, and
    the window treats that answer as completion. Progress frames carry
    `{stage, message, percent}`, and the window looks for `done` and `error` keys
    that the core never sends, so every frame sets the state back to running.
    There is no frontend test for this flow.
    `shell/src/hooks/useModelSelection.ts` (`handleStartLocalSetup`) ·
    `shell/src/App.tsx` (the `model.localSetupProgress` subscriber) ·
    `agent_core/main.py` (local setup)

21. **The delete preview never reaches the permission card.** The core computes
    "About to delete 1,240 files in 12 folders." and puts it on the card as
    `preview`. `normalizePermission` in `App.tsx` copies the tool id, label,
    description, risk tier and arming block, and drops `preview`. That function
    is the only path from the wire to `PermissionCard`, which would render the
    line. Rendering the real App with the core's card shows the description and
    no preview. The BUILD-LOG and KNOWN-GAPS entries that describe the line as
    shipped are false about the app. PR #156 (`claude/permission-card-command`)
    already copies `preview` in `normalizePermission`. Strike this entry after
    that PR merges and the check above has been re-run.
    `shell/src/App.tsx` (`normalizePermission`)

### P2 — trust and lifecycle

22. **After Stop, a tool that already has permission still runs.** Live repro:
    allow `save_file` in one turn, then send a message whose model answer takes
    three seconds and asks for `save_file` again, and press Stop after one second.
    Stop answers `endedRequests: 0`, the file is written, and Undo offers to undo
    it. The stop flag is only read where a card would be raised, so every path
    that needs no card keeps going for up to 25 rounds. That covers session
    grants in Simple, auto-grants in Developer, trusted file edits, and
    `run_command` under Custom's "Never ask". The routine engine has the same
    shape. `protocol.py` says Stop ends the turn's consent.
    **Reproduced by the coordinator.**
    `agent_core/orchestrator.py` (`_run_tool_calls`) ·
    `agent_core/permissions/gate.py` · `agent_core/routines/engine.py`

23. **After Stop, the rest of the stopped answer is added to the front of the next
    reply, and its steps appear in the next turn's work panel.** The core runs
    one job at a time and Stop does not end the running job. Stream chunks carry
    only text, so the window appends them to whatever turn is current. Rendering
    the real App with Send A, Stop, Send B shows B's bubble holding A's remaining
    answer followed by B's. The core stores the two answers separately, so a
    reload shows a different thread from the one the person read.
    `shell/src/hooks/useTurn.ts` (`appendStreamedText`) · `shell/src/App.tsx`

24. **Stop, then New chat, then Send makes the new message and its answer
    vanish.** The New chat and open-conversation requests queue behind the
    stopped job. When they land, their `.then` handlers reset the thread with no
    check that a newer turn has started since. The message is stored in the new
    conversation and the sidebar is not refreshed. The same shape applies to
    opening another chat.
    `shell/src/hooks/useConversations.ts` (`newConversation`, `loadConversation`)

25. **A "Not now" outlives its turn and refuses widgets and routines without a
    card.** Denials are cleared only when a chat turn starts or the profile
    changes. `widget.run` and `routine.run` never clear them. After one denial, the
    next Run on a widget or routine is refused with "You declined a permission it
    needs." and no card, until the person sends a chat message. A chat denial
    refuses a later widget Run the same way.
    `agent_core/permissions/gate.py` (`clear_denials`) · `agent_core/rpc/widgets.py` ·
    `agent_core/routines/engine.py`

26. **In Custom, an "Ask once" approval survives changes that should end it.**
    Removing a tool server revokes nothing, and a different server saved under
    the same name gets the same tool ids, so its calls go through on the old
    approval. Tightening the guard to "every time" and loosening it again brings
    the old approvals back, so `rm -rf build` ran on an approval given for `ls`.
    `agent_core/permissions/gate.py` · `agent_core/rpc/mcp.py` (remove) ·
    `agent_core/rpc/guards.py`

27. **The summary request for a long chat is sent without redaction.** When a
    chat crosses the context threshold, `build_summary_request` renders the raw
    message text and `rpc/conversation.py` sends it straight to the provider,
    without `redacted_for_model`. A key pasted earlier in the chat went out in the
    summary request, while none of the ordinary turn requests carried it. This is
    the only direct `provider.send` outside the orchestrator.
    **Checked in the source by the coordinator.**
    `agent_core/context_continuation.py` · `agent_core/rpc/conversation.py`

28. **The screening rule for role markers takes quadratic time, and a shared
    routine file or a tool server can use it to freeze the engine.** The patterns
    `<\s*/?\s*(…)` and `\[\s*/?\s*INST` put two unbounded whitespace runs side by
    side. A "<" followed by spaces takes 0.54 s at 5,000 characters, 2.2 s at
    10,000 and 8.7 s at 20,000. A routine file inside every import bound took 92 s
    at 64 KB and runs twice (preview and confirm). A 15 KB tool schema costs
    5.3 s per tool on each "Check now". Everything else waits on the same worker.
    Making both gaps possessive (`\s*+/?\s*+`) brings 100,000 characters to
    0.004 s and the screening tests still pass. The module header says no rule
    has an unbounded quantifier. **Reproduced by the coordinator.**
    `agent_core/screening.py`

29. **A web page can hold the engine for minutes or hours.** Two separate causes.
    First, `read_web_page` passes no `total_timeout` to `open_vetted`, and its
    read loop never checks a clock, so a server sending one byte every 5 s kept a
    fetch alive for 60 s under a 20 s socket timeout. Second, the HTML-to-text
    step is quadratic on markup with many unclosed `<`. `"<a " * k` took 0.08 s
    at 3 KB, 0.7 s at 9 KB and 8.3 s at 30 KB, and the tool accepts 2 MB, which
    extrapolates to hours. If the first parse finds no text, the step parses the
    whole page a second time. The tool is available in Simple and to a paired
    phone, and the address is often chosen by page content.
    **Parse timing reproduced by the coordinator.**
    `agent_core/tools/read_web_page.py` (`_read_capped`, `_parse`, `_extract`)

30. **The calculator hangs on a large exponent, and an answer over 4,300 digits
    breaks the turn.** `9**9**7` takes 3.3 s and `9**9**8` took 114 s, with no
    bound on exponent or result size. The tool returns a raw `int`, and turning an
    int over 4,300 digits into a string raises outside the tool's error handling.
    Live repro: the model asks for `7**6000`, and `sendMessage` fails with "Check
    your internet connection and that your API key is still valid", which is the
    wrong diagnosis. In a routine the same raise leaves the run recorded as
    `running` with no completion time. The calculator is available to a paired
    phone. **Reproduced by the coordinator.**
    `agent_core/tools/calculator.py` · `agent_core/orchestrator.py` ·
    `agent_core/routines/engine.py`

31. **A shared routine file can carry a command, and a `create_automation` step
    saves it as an automation with no card.** Import and export only look for a
    step-level `command` key. A step `{"tool_id": "create_automation",
    "args_template": {"command": "curl … | sh"}}` imports in any profile, and
    running the routine in Developer saved the automation with no permission card.
    A `run_command` step with the command inside `args_template` also imports and
    re-exports, and that one does get a card when it runs. Owner decision 3A says
    the format cannot express a command. Arming still needs the typed code.
    `agent_core/routines/portable.py`

32. **Text from an attached document can go to the web from a routine without the
    taint card.** `FILE_READING_TOOL_IDS` predates `search_knowledge`, so a
    `search_knowledge` step never taints a later `read_web_page` step. With
    `read_file` the card says "This step would send text from the file … to the
    web." With `search_knowledge` there is no such line and no forced card.
    `agent_core/routines/taint.py`

33. **Reverting a file after the person renamed it recreates the old name and
    leaves Addison's changes in the renamed file.** Addison edits `notes.txt`, the
    person renames it to `final.txt`, and Addison edits `final.txt`. The two edits
    join one chain through the file's identity. Revert writes the original bytes
    to `notes.txt`, `final.txt` keeps both edits, both rows are marked reverted,
    and the success sentence names the wrong file.
    `agent_core/snapshots/file_revert.py`

34. **A phone message that waited behind other work is declined with "Addison
    wasn't running when you sent that".** Staleness is measured from the moment
    the job leaves the worker queue. `receivedAt` is stamped at hand-off and read
    by nothing. A desk turn waiting on an unanswered card, which is when the
    person is using the phone, holds the queue long enough to trigger this.
    **Checked in the source by the coordinator.**
    `agent_core/rpc/channels.py` · `agent_core/channel_service.py`

35. **Switching a phone connection off and on during a long poll lets the old
    loop act on the new one.** The stopped thread writes status and calls
    `_token_rejected` without checking its own stop event, and `_token_rejected`
    removes the new loop's stop event. With a 401 the new loop stops and the
    status blames the token. With Telegram's 409 for a second `getUpdates`, a
    healthy loop reports an outage for up to one 50 s poll.
    `agent_core/channel_service.py`

36. **A link swapped in between the shell's check and its write puts the write
    inside Addison's own data folder.** `write_workspace_path` checks the path
    with `refuse_addison_data_dir` and then calls `std::fs::write`, which resolves
    the path again and follows whatever link is there at that moment. With a
    background job flipping `notes.txt` between a file and a link to
    `snapshots/genesis.json`, the write landed in the sidecar on attempt 13. An
    approved command can leave such a job behind in Developer. The seatbelt
    refuses the command's own write into the data folder, and the shell then does
    the write for it. `restore_workspace_path` and the read, digest and adopt
    paths share the check-then-open shape. The fix is to open without following
    links and check the opened descriptor's real path.
    **Reproduced by the coordinator.**
    `shell/src-tauri/src/filesystem.rs`

37. **The `/System/Volumes/Data/…` spelling of a folder walks past the data-folder
    floor and the LaunchAgents fence.** macOS `realpath` does not fold firmlinks.
    Under that spelling the shell reads and writes the database, and the core's
    `trust_refusal` returns `None` for the data folder, for `~` and for
    `~/Library`. So a trusted folder spelled that way lets `write_project_file`
    reach places that are refused under the ordinary spelling. It needs the
    person to trust a folder typed with that prefix. The seatbelt is not affected.
    `shell/src-tauri/src/filesystem.rs` (`canonical_lossy`) ·
    `agent_core/policy.py` (`_canonical`)

38. **A command that prints more than 512 KiB is killed partway instead of having
    its output shortened.** When the capture buffer is full the reader stops
    reading and drops the pipe, so the command's next write gets SIGPIPE and it
    exits 141. A verbose build, a test run or `npm install` can stop half done.
    `shell/src-tauri/src/exec.rs` (`drain`)

39. **While a file or folder dialog is open, nothing moves between the engine and
    the window. A save that takes over a minute is written and reported as
    failed.** `shell.pickFile`, `shell.pickDirectory` and `shell.saveNewFile` are
    awaited inline on the shell's stdout pump, so stream chunks, cards and other
    requests stop until the dialog closes. The core gives up on these calls after
    60 s. After that, `save_file` creates the file, records no undo row and tells
    the model it failed, and routine export writes the file and tells the window
    it failed. Routing these three methods off the pump, as the keychain calls
    already are, removes the stall.
    `shell/src-tauri/src/agent_process.rs` · `shell/src-tauri/src/filesystem.rs` ·
    `agent_core/shell_bridge.py`

40. **After a long chat continues automatically, Edit and resend is refused on
    every message.** The continuation switches the core to a new conversation id
    and the send reply does not say so. The window keeps the old id, and rewind
    runs against the new conversation, where none of the visible message ids
    exist. The answer is "Couldn't find that point to rewind to." Renaming the
    chat from its header also targets the old conversation.
    `agent_core/rpc/conversation.py` · `shell/src/hooks/useConversations.ts`

41. **After the engine restarts, the chat looks busy for 15 minutes, the dead
    engine's card stays clickable, and the next message goes to a new, empty
    conversation.** Pending requests are not settled when the shell reports
    restarting and ready, so the turn waits for its 15-minute timeout. Allow on
    the old card gets `{ok: false}` from the new engine, and the window ignores
    that answer. The new engine starts its own conversation, so the next answer
    has no memory of the thread on screen and is stored somewhere else.
    `shell/src/ipc/client.ts` · `shell/src/App.tsx` (the `ready` handler,
    `handleRespondPermission`)

42. **Running a routine or widget says "try again" after two minutes while the run
    continues, and trying again runs it twice.** `routine.run` and `widget.run`
    use the 120 s default timeout, and the run they wait on can sit on a card with
    no ceiling. The Run button comes back and a second run queues behind the
    first.
    `shell/src/ipc/client.ts` · `shell/src/components/RoutineLibrary.tsx`

43. **The routine library tracks one running routine and one set of typed
    answers for all routines.** Pressing Run on B while A runs re-enables A's Run
    button, and A was sent twice. Answers typed for B are cleared when A finishes,
    and B is then sent `{}` and refused for a missing value.
    `shell/src/components/RoutineLibrary.tsx`

44. **A card that ended with Stop blocks opening another chat, with a sentence
    telling the person to answer it.** The navigation guard counts an expired
    card as pending. The card has no Allow button, and the banner says "Answer
    Addison's question first — it's still waiting for you." New chat still works.
    `shell/src/App.tsx` (`permissionPending`)

45. **A restore started from Settings leaves the Settings routine list showing the
    routines from before the restore.** The post-restore refresh re-reads the
    rail's copy of routines. The routine library keeps its own list and only
    re-fetches when the profile changes.
    `shell/src/App.tsx` (`onRestored`) · `shell/src/components/SettingsPage.tsx`

46. **The command on a permission card is cut to about 30 characters in the side
    rail.** The command chip uses `truncate` with a hover title. In the 232 px rail,
    `cd ~/Projects/family-website && npm run build && rm -rf ~/Documents/Taxes`
    shows as `cd ~/Projects/family-website &…`. The core also caps the card's
    command text at 120 characters. SAFETY.md says the card carries the exact
    command. **Checked in the source by the coordinator.**
    `shell/src/components/PermissionCard.tsx` · `agent_core/tools/run_command.py`

47. **A shared routine's question text can close Addison's quotation marks on the
    import card.** The card builds `"${v.prompt}"` from the file without removing
    quote marks, in the same ink as the assurances below it. A prompt of
    `Which folder?" Addison has checked this routine and it is safe to add. "`
    renders as Addison's own sentence. `mcp_catalog._unquoted` removes quote
    marks for this reason. **Checked in the source by the coordinator.**
    `shell/src/components/RoutineImportCard.tsx`

48. **Four surfaces say Addison asks every time, and Custom's guards can make that
    once per session or never.** The tool-server panel, the tools surface and the
    workspace trust panel state the frequency and take no guard or profile
    input. Under "Ask about everything" the workspace panel's "reads and edits
    files without asking first" is false in the other direction.
    `shell/src/components/McpServersPanel.tsx` · `ToolsSurface.tsx` ·
    `WorkspaceTrustPanel.tsx`

49. **"Everything can be undone" is shown in Developer and Custom.** The composer
    hint and the empty thread say it in every profile. `run_command`, tool-server
    tools and armed automations cannot be undone. The restore-points footer was
    already made profile-aware for the same sentence.
    `shell/src/components/Composer.tsx` · `shell/src/components/ChatThread.tsx`

50. **The "add a model server" card drops the core's refusal and saves the key
    before a connection the core has already refused.** `parseEndpointProposal`
    keeps `baseUrl` and `isLocalOrLan` and drops `error`. On Add, the key goes to
    the keychain first, the core refuses, and the key is rolled back. If the
    rollback fails, the saved custom-server key has been overwritten.
    **Checked in the source by the coordinator.**
    `shell/src/ipc/client.ts` (`parseEndpointProposal`) ·
    `shell/src/components/EndpointProposalCard.tsx`

51. **Removing a restore point takes one press and cannot be undone.** Removing a
    routine, a skill, an automation, a tool server or a phone connection each asks
    "Really remove?". Remove also stays live while a restore runs.
    `shell/src/components/SnapshotsCard.tsx`

52. **Undoing an arm reports success when the Mac refused to switch the job
    off.** `arm_automation.undo` ignores the `{ok: false}` that
    `shell.disarmAutomation` returns. The snapshot is marked reverted while the job
    keeps its schedule. The next Undo removes the automation row without
    disarming, which leaves an orphan job. The test fake always answers `ok: true`.
    `agent_core/tools/arm_automation.py` · `agent_core/tools/create_automation.py`

53. **Writing an automation again after a restore makes the new one look armed
    while the old command still runs.** A restore can remove an armed row while
    launchd keeps the job. The label for a new automation is chosen against rows
    only, so it can reuse that job's label. Settings decides "on" by label, so the
    new command shows as on without its code ever being typed, and launchd keeps
    running the old command.
    `agent_core/tools/create_automation.py` · `agent_core/automations.py`

54. **When two providers list the same model id, the chat can go to a different
    server than the picker names.** The router pool is keyed by bare model id.
    The picker keeps the first provider it saw and the router keeps the last. With
    a custom server connected first and OpenAI second, the picker says "Your own
    server" for `gpt-4o` and the turn goes to `api.openai.com` under the OpenAI
    key. Disconnecting the named provider leaves the traffic where it was. The
    same collapse happens between a custom Ollama and a local model name. Once
    local models reach the Settings lists (the fix for 20), a custom server at
    the Ollama address lists the same ids as the local role, and the custom
    chain builder shows the model twice under one React key.
    `agent_core/providers/router.py` · `agent_core/models_catalog.py` ·
    `agent_core/rpc/providers.py`

55. **An answer with no text breaks every later turn in that chat on Anthropic and
    Gemini.** The orchestrator stores the empty answer. On the next turn Anthropic
    receives `{"role": "assistant", "content": ""}` and Gemini receives
    `{"role": "model", "parts": []}`, and both vendors refuse those. A safety
    stop, a refusal, a stream that failed before any text, or thinking that used
    the whole output allowance all produce an empty answer. `conversation.load`
    already skips empty rows, and the live path does not.
    `agent_core/orchestrator.py` · `agent_core/providers/anthropic_provider.py` ·
    `agent_core/providers/google_provider.py`

56. **`snapshot_now` deletes old restore points, and Developer runs it without a
    card.** The tool calls `capture()`, which prunes by default. Fifty captures
    after a 40-day gap removed the person's own restore point, and only the
    genesis row was left from before the gap. SAFETY.md and the tool's docstring
    say it may only ever add a row, and that promise is the reason it is LOW with
    no undo. The source test checks attribute names in the tool file and cannot
    see the prune inside `capture()`. **Reproduced by the coordinator.**
    `agent_core/tools/snapshot_now.py` · `agent_core/snapshots/snapshot_manager.py`

57. **Several floor tests stay green when the property they guard is broken.** Each
    of these mutations passed the whole suite (Python 2,182 passed; vitest 1,032):
    - C6: filtering the one-action restore's walk by `created_in_mode` only while
      in SAFE, and the same filter on `SnapshotManager.delete`. SAFETY.md says the
      source test reads SQL in `snapshot_manager.py`, which contains none.
    - G2: starting a self-waking thread through an aliased `Thread`, through
      `_thread.start_new_thread`, or through a `ThreadPoolExecutor`.
    - G1: putting the key into the Anthropic connect error string, or printing it
      from `_provider_key_getter`. Both live in closures inside `main()` that no
      test reaches.
    - G4: an anchor mint that raises instead of returning `None`.
    - Invariant 2: a tool that subclasses `UndoableTool` inherits a body-less
      `undo` and registers at HIGH into the SAFE view, and the
      `__isabstractmethod__` branch can never fire. `allow_missing_undo=True` alone
      also puts a HIGH tool into the SAFE view, and only literal set pins in
      `test_profiles.py` stop it.
    - Invariant 4: a widget spec carrying its own `capabilities` key.
    - The module boundary: `from agent_core import providers` and
      `from .. import providers` inside `tools/` or `routines/`.
    - `test_live_model_registration`: validating with the live list and then
      registering hard-coded ids through an assignment.
    No live breach exists today. Each item is a hole in the test that would catch
    one. `tests/test_ipc_snapshots.py` · `tests/test_g2_no_self_trigger.py` ·
    `tests/test_module_boundaries.py` · `agent_core/tools/registry.py`

58. **Withdrawing consent, or deleting, reports success when the core refused.**
    Stop trusting a folder shows "Addison will ask first again in …" when the core
    answered `{ok: false}`. Removing a phone connection shows "Addison has
    forgotten <name>." after a refusal. Deleting a skill or a widget ignores the
    core's "couldn't save a restore point, so it didn't delete anything". The
    tests for these hooks build state by hand, and mutations that remove the
    refusal handling all survive. The grant paths have real-hook tests and their
    mutations are killed.
    `shell/src/hooks/useWorkspace.ts` · `useChannels.ts` · `useSkills.ts` ·
    `useWidgets.ts` · `useGuards.ts` · `useSnapshots.ts`

59. **The macOS-only tests never run in CI.** `ci.yml` has Ubuntu and Windows
    runners and no macOS one. 43 of the 201 Rust tests are macOS-only, including
    every seatbelt test in `exec.rs` and every launchd test in `automation.rs`,
    such as `an_approved_command_cannot_delete_the_recovery_floor` and
    `the_plist_never_sets_run_at_load`. `gates.sh` says CI runs the Rust job on all
    three platforms. **Checked by the coordinator.**
    `.github/workflows/ci.yml` · `scripts/gates.sh`

60. **Windows only: the engine reads the shell's messages in the ANSI code page.**
    `main.py` reads `sys.stdin`, which Python on Windows decodes with the locale
    code page, and the shell writes UTF-8. Fed the shell's bytes, "Díky, a co
    zítra?" arrived as mojibake under cp1250, and "Řekni mi počasí" raised
    `UnicodeDecodeError` and ended the read loop. After one respawn the engine
    stays down. Text read from files through the shell would be written back
    corrupted. Wrapping `sys.stdin.buffer` as UTF-8 fixes it. This was simulated
    on macOS, because nothing in the port has run on Windows.
    `agent_core/main.py`

61. **The streaming answer re-parses the whole answer on every frame, so long
    answers load the thread that handles input.** Each frame parses the full
    prefix, and the growing block is re-rendered with highlighting on each
    completed line. Per-frame parse cost was 1.8 ms at 2 KB, 19.9 ms at 20 KB and
    342 ms at 200 KB. A 20 KB mixed answer used about a quarter of the main thread
    for its whole stream. A 20 KB table took 42 ms per frame, which is more than
    the 38 ms frame interval. The Anthropic and OpenAI adapters cap output near
    16 KB, and Google and Ollama set no cap.
    `shell/src/lib/streamMarkdown.ts` · `shell/src/components/StreamingMarkdown.tsx`

91. **When a turn fails after a tool has run, the transcript hides the action.**
    When `run_turn` raises, the `except` in `rpc/conversation.py` removes
    everything the turn added (`del self.conversation.messages[pre_turn:]`),
    including a tool call that already ran and its result. The file is on disk and
    the chat shows only the person's message and an error. Live repro, found while
    fixing 17: the fake model asks for `save_file`, the card is answered Allow, and
    the model then hangs until the fallback budget ends the turn. The file is
    written and the stored transcript holds only the user message. Any failure
    after a tool round does the same, such as a provider outage or a rejected
    request on the second send. The removal exists because an unpaired tool call
    makes the provider refuse every later request, so a fix has to keep the pair
    intact and still leave the conversation sendable. Added 2026-09-30, after the
    hunt.
    `agent_core/rpc/conversation.py` · `agent_core/orchestrator.py`

93. **A model set up on this computer is forgotten when the app restarts.** The
    only place that registers a local model with the router is the end of
    `_run_local_setup`, which runs in the process that did the download. The
    router built at startup registers none. After a restart the core's
    `localModels` list is empty, the picker no longer offers the model, and the
    Settings row offers to set it up again although it is still installed in
    Ollama. Found by the review of the fix for 20. Added 2026-09-30, after the
    hunt.
    `agent_core/main.py` (`_run_local_setup`, router construction) ·
    `agent_core/providers/router.py`

### P3 — quality

62. **Every automatically approved step shows twice in "Addison's work" in
    Developer.** The core sends an `autoGranted` frame and then the ordinary step,
    and the window appends both and drops the `autoGranted` flag. This is the
    cause of the double-listed step recorded as an open question on 2026-08-21.
    Routine runs double the same way. `shell/src/App.tsx` (`normalizeActivity`) ·
    `agent_core/main.py`

63. **A phone turn's steps appear in the desktop work panel.** `on_activity` is
    not limited by surface, so a remote turn's calculator step reaches the desk as
    `tool.activityUpdate`, and in Developer an extra auto-grant line comes with
    it. `rpc/channels.py` says remote turns do not use that channel.
    `agent_core/orchestrator.py`

64. **Three of the four streaming parsers treat a cut-off stream as a finished
    answer.** When the stream stops mid-answer, OpenAI, Google and Ollama return
    the partial text with `finish='stop'`. Anthropic raises. No Retry or Continue
    is offered. A custom server that ignores `stream: true` and returns one JSON
    body produces no text at all.
    `agent_core/providers/{openai,google,ollama}_provider.py`

65. **A model the provider refused sinks in the picker and keeps its place in
    routing.** The rank-99 downgrade in `mark_refused` is only applied to the wire
    rows. After the 60 s cooldown or a restart, the refused model is the first
    fallback again. `agent_core/rpc/routing.py` · `agent_core/rpc/models.py`

66. **The Google model list reads only the first page.** `nextPageToken` is
    ignored and the default page is 50 models. The Anthropic list pages.
    `agent_core/providers/google_provider.py`

67. **The Gemini token count leaves out thinking tokens.** `thoughtsTokenCount` is
    billed as output and is not added. `agent_core/providers/google_provider.py`

68. **A tool server that answers over SSE and leaves the stream open is reported
    as "didn't answer in time".** The client reads to the end of the stream before
    parsing, although the answer arrived in the first chunk.
    `agent_core/mcp_client.py`

69. **Custom model servers: the Ollama example address fails, and `HTTP://` is
    refused.** Connecting `http://box:11434` requests `{base}/models`, which
    Ollama answers with 404. The comment in `main.py` says `/v1/models`. The
    scheme check is case-sensitive, so `HTTP://…` is told it must start with
    `http://`. `agent_core/providers/openai_provider.py` ·
    `agent_core/rpc/providers.py` (`_base_url_problem`)

70. **The server-description quote on a tool-server card only removes “ and ”.** A
    look-alike mark such as U+02EE appears to close the quote.
    `agent_core/mcp_catalog.py` (`_QUOTE_MARKS`)

71. **On a tool-server card, the server's words after "run: " are drawn as the
    command.** `splitCommand` takes the first `run: ` anywhere in the description,
    including inside the server's quoted text. `shell/src/components/PermissionCard.tsx`

72. **Revoking trust on a folder whose path has since become a link answers ok and
    keeps the row.** The handler resolves the path it is handed again.
    `agent_core/rpc/workspace.py`

73. **Some Python error text reaches the person as written.** A `KeyError`
    sentence arrives wrapped in quotes ("\"That routine doesn't exist any
    more.\""). A deeply nested routine file shows "maximum recursion depth
    exceeded while decoding a JSON array", because `RecursionError` is a
    `RuntimeError` and `_plain` passes those through.
    `agent_core/rpc/routines.py` · `agent_core/main.py` (`_plain`)

74. **Redo is offered after undoing a file edit, and it always fails.**
    `undo_last` puts every undone snapshot on the redo stack whether or not the
    tool can redo. The same happens for `create_automation`, `arm_automation` and
    `draft_message`. A test calls this "legitimately redoable".
    `agent_core/snapshots/undo_manager.py`

75. **The taint card names a picked file by its opaque handle.** The real handle is
    a uuid4, so the card would read "text from the file '3f9a…'". The test
    fixtures pass a path as the handle. `agent_core/routines/taint.py`

76. **The live-database guard can be passed with an ordinary respelling of the
    path.** `.ADDISON` (APFS is case-insensitive), `file://localhost/…` and a
    `%2E`-encoded `file:` URI all open the protected file.
    `agent_core/live_db_guard.py`

77. **A malformed Mermaid diagram leaves Mermaid's error graphic in the page.**
    Mermaid 11.16 throws before it removes its temporary element, and
    `suppressErrorRendering` is not set. The window then scrolls to show "Syntax
    error in text" under the app, one more per failed render. Separately,
    `flowchart.htmlLabels: false` is ignored by this Mermaid version, so node
    labels are still HTML in a `foreignObject`, and the test named "draws labels as
    SVG text" asserts the config value, not the output.
    `shell/src/lib/mermaidTheme.ts` · `shell/src/components/MermaidDiagram.tsx`

78. **A half-written message is lost in two ways.** Ask or Explain on selected text
    replaces the draft instead of adding to it, and so do the suggestion chips and
    the Settings "Arm…" and "Ask this here" buttons. The draft is also discarded
    when the person opens Settings, Tools or Restore points and comes back.
    `shell/src/components/Composer.tsx` · `shell/src/App.tsx`

79. **A timer widget shows too much time for a moment when it starts.** Its clock
    value is taken when the widget mounts, so a 5:00 timer showed 1:05:00 for one
    second. `shell/src/components/WidgetRail.tsx`

80. **The sidebar gets slow with many chats.** Expanding a group forces a layout
    per row inside a loop: 159 ms at 250 chats, 3.3 s at 1,000 and 9.2 s at 2,000
    in Chromium. While "Earlier" is expanded, the whole app re-renders on every
    scramble frame and each row builds two date formatters: 177 ms per render at
    2,000 chats in jsdom. `shell/src/components/Sidebar.tsx` · `shell/src/lib/time.ts`

81. **The "add a server" hint check is quadratic on the person's own message.**
    `"add item to list\n"` repeated took 0.03 s at 17,000 characters, 0.77 s at
    85,000 and 23 s at 340,000, on the main thread after every turn.
    `shell/src/hooks/useOffers.ts`

82. **A chat rename snaps back when a list refresh was queued before it.** The
    refresh's stale copy overwrites the new title until the next refresh.
    `shell/src/hooks/useConversations.ts`

83. **An arming code typed with any non-ASCII character breaks the turn.** `hmac.compare_digest` raises on non-ASCII text, so `ABČ-DEF` or
    full-width letters end the turn with the "check your internet connection"
    message instead of costing one attempt. The pairing check has the same shape,
    so a stranger's "Dobrý den" during a pairing window spends no attempt.
    `agent_core/automation_nonce.py` · `agent_core/channel_pairing.py`

84. **The delete preview overstates deletes that will fail.** `rm build` on a
    directory is previewed as "About to delete 30 files in 3 folders." and then
    exits 1 with nothing deleted. The preview walk also runs for commands whose
    card will not be shown. `agent_core/delete_preview.py`

85. **A notice about a picture is joined to the previous sentence.** The vision
    notice goes to the frontend sink rather than the turn's relay, so it reads
    "Let me look at the photo.This file is a picture…".
    `agent_core/orchestrator.py`

86. **Interface text that breaks the house rules.** The routine run panel prints
    tool ids (`calculator`, `read_web_page`) under each step, in Simple too.
    Settings says "Everything lives on this computer. Nothing leaves it without
    asking you first.", which is false for chats sent to a cloud provider. The
    phone panel's "Everything else stays on this computer." leaves out the
    provider that answers the phone's message. Several Settings rows cut prose,
    error lines and folder paths to one line with no title, including the "Trust
    this folder?" confirmation and the Custom guard options.
    `shell/src/components/RoutineLibrary.tsx` · `SettingsPage.tsx` ·
    `ChannelsPanel.tsx` · `WorkspaceTrustPanel.tsx` · `CustomGuardPanel.tsx`

87. **Model output can put a remote image in the chat.** The Markdown renderer has
    no rule for images. The content policy blocks the request, so the person sees
    a broken image. `shell/src/components/Markdown.tsx`

88. **Knowledge search scans every chunk in Python on the engine thread.** It took
    81 ms at 1,000 chunks, 815 ms at 10,000 and 8.5 s at 100,000, and the query
    norm is recomputed for every candidate. Nothing on master adds documents, so
    this cannot happen yet. It becomes P1 when phase 3 (PR #157) lands.
    `agent_core/knowledge/index.py`

89. **Work and storage that nothing uses.** `list_conversations` computes
    `messageCount` with a join over every message after every turn, and nothing
    reads it: 24 ms at 2,000 chats against 2 ms without it.
    `conversations.provider_id` is always written as `"primary"` and never read.
    `auto_grants` in the gate grows on every auto-grant and is never read. No
    production code calls `DirectAPIProvider`, `ModelRouter.register`,
    `ChannelService.is_listening`, `PermissionGate.revoke`,
    `ModelRouter.select_local_model`, `Store.list_provider_attempts` or
    `Store.get_widget_state`. `revoke` also differs from `revoke_all`. The TS types
    `PermissionStatus`, `JsonRpcRequest`, `JsonRpcResponse` and `MonacoApi` have
    no users. `routine.list` sends `importedAt`, which nothing reads.
    `policy._command_tokens` removes duplicates with a list scan, which is
    quadratic past realistic command lengths (396 ms at 100,000 characters).

90. **Documents and docstrings that the tree contradicts.**
    - `docs/SAFETY.md` says routine import is not built, as the reason disabled
      rows leak nothing. It has been built since 2026-08-15.
    - `docs/addison-engineering-spec.md` §4.2 says Simple registers exactly eight
      tools. The SAFE view has twelve. §10 lists messaging channels, routine
      sharing and screening as deferred.
    - `docs/README.md` says knowledge is proposed and nothing is built, and
      KNOWN-GAPS speaks of two phases still to build. Phases 1 and 2 are merged.
    - The plans are counted as twelve in `CLAUDE.md`, `docs/README.md` and
      `tests/test_docs_drift.py`, and there are thirteen. `docs/README.md` says ten
      of twenty claim rows, and there are twenty-one.
    - Docstrings still state retired facts: `routines/model.py` (routines hidden
      in SAFE), `rpc/routines.py` (file tools are open-only), `routines/engine.py`
      (the remote floor is empty; file tools are not routine-exposed),
      `tools/create_automation.py` and `main.py` (both still call arming a later
      phase, and it shipped in step 8 phase 3),
      `widgets.py` (command widgets never surface in Simple), `App.tsx` (the
      workspace card shows only in Developer and Custom), `policy.py` ("when that
      lands" for the seatbelt), `rpc/automations.py` (routines read the stamp),
      `search_knowledge.py` (the orchestrator screens its results), and
      `permissions/gate.py` (a Settings revoke control that does not exist).
    - `doc_claims.py` scans `.md` files only. Run over `.py` and `.ts`, its own
      rows already flag four of the docstrings above. Several rows also miss
      the sentence their own comment gives as the example.
    `docs/` · `tests/doc_claims.py`

92. **The card for a web page read in a routine calls the address a command.**
    When a routine's `read_web_page` step takes the per-invocation card, the card
    reads "This time it wants to run: www.example.com". `_card_consequence` falls
    back to the `run_command` wording whenever a call has a detail and the tool
    has no sentence of its own, and `read_web_page` has none. The frontend also
    splits on the "run: " prefix and draws the address as a command. Found while
    fixing 21, once the card's preview line started to show. Added 2026-09-30,
    after the hunt.
    `agent_core/main.py` (`_card_consequence`) · `agent_core/tools/read_web_page.py`

### Unconfirmed (reasoned from the code, not reproduced)

- The dialog stall in 39 could become a permanent deadlock if the window sends
  about 64 KB while a dialog is open, because the pump's reply needs the stdin
  lock that `send_to_core` holds. Proving it needs the live shell.
- A permission card may be lost across a webview reload, because nothing asks
  `permission.pending` on mount while no turn is marked working.
- `_recover_from_sidecars` moves the damaged database aside before the swap. If
  the swap fails, the next open could treat the install as new and mint a verified
  genesis row.
- If `undo_manager.record` raises after a tool has run, no undo row is written and
  the exchange is removed.
- Ollama models without tool support may drop replayed tool messages, and tool
  schemas with `$schema` or `additionalProperties` may be refused by Gemini.
- Bidirectional override characters are not stripped from `run_command` details or
  automation commands. No single-line command was found that hides a destructive
  part without visible noise.

## Whole-app test pass, 8–9 August 2026

This pass came from the QA artifact "Addison — whole-app test pass", baseline
`7733dbb`.

**Re-run pass 2026-08-21** (current master `0997410`, debug bundle + live app,
remote-driven + owner-driven): **all fifteen entries re-ran green** and are
struck below, each with what the re-run actually showed. The same session also
ran the artifact's never-run sections — the floors, Custom + the G4 anchor, C6,
the Code screen's restart/theme/width checks, and both engine kills — all
green; the artifact records each. It also found the environment trap the
artifact's first panel warns about, twice over: a stale Aug-9 debug bundle
answering under the current frontend, and one **new cosmetic finding** — the
"Addison's work" panel double-lists a step while the turn streams (one
`calculator` audit row, two identical bullets); the reloaded transcript shows
it once, so persistence is correct.

### P1 — broken features

1. ~~**Arming an automation from chat can never succeed — the id is never in the
   conversation, in any flow.**~~ **RE-RUN GREEN 2026-08-21.** Both dead flows
   work: create-then-"arm Heartbeat" in one conversation reached the full
   keyword card (fresh nonce; `DJH-WH9`), and the Settings "Arm…" seeded
   sentence reached the same card with a different fresh nonce (`7QU-HVT`).
   A real end-to-end arm ran the same evening on a Gemini turn: create → card →
   typed code → plist written → Remove disarmed and deleted it, `launchctl`
   clean, no first run (G2 held). Root cause had been pinned 2026-08-11:
   `create_automation`'s result text never carried `row.id`, and
   `arm_automation` resolved strictly by UUID; fixed by PR #98 (id surfaced,
   honest refusal, unique-name resolution).
   `agent_core/tools/create_automation.py` · `agent_core/tools/arm_automation.py` ·
   SettingsPage AutomationsSection · artifact §07

2. ~~**Gemini 3.x multi-step tool turns always fail.**~~ **RE-RUN GREEN
   2026-08-21, live.** `gemini-3.5-flash` on the owner's key completed the
   artifact's own §02 check (the Probe automation prompt): two tool calls, a
   permission card answered mid-turn, and a closing sentence. No 400, no
   misattributed "That key doesn't work". Fixed by PR #109 (the adapter replays
   `thought_signature`).
   `agent_core/providers/` google adapter · artifact §02

### P2 — trust and lifecycle

3. ~~**A pending approval card can stay invisible behind "Working…".**~~
   **RE-RUN GREEN 2026-08-21** (owner-driven): a destructive-command card left
   deliberately unanswered stayed rendered the whole wait, every card of ~12
   other turns rendered the moment the engine asked, and the shipped behaviour
   goes further than the fix promised — navigating away while a card waits is
   held, with a plain banner ("Answer Addison's question first — it's still
   waiting for you."), which makes this failure class structurally
   unreachable. (PR #110: below-the-fold diagnosis + watchdog.)
   Frontend card rendering / turn state · artifact §04

4. ~~**An approval card outlives its stopped turn and stays fully actionable.**~~
   **RE-RUN GREEN 2026-08-21.** Stop pressed with a "Run a command" card
   pending: the reply settled to "(Stopped.)" and the card's controls were
   replaced by "This request ended when you stopped the answer." Decided and
   enforced as card-dies-with-the-turn (PR #99, plus #110's interaction fix).
   Gate ↔ turn lifecycle · artifact §04

5. ~~**"Save as routine" is lost on conversation reload.**~~ **RE-RUN GREEN
   2026-08-21.** Quit + relaunch + reopen: the work panel and the "Save as
   routine" link both survive, and the plan offered after reload is the same
   turn-scoped plan. (Fixed by PR #100.)
   Frontend work-panel state · artifact §06

6. ~~**Simple cannot edit existing files at all.**~~ **RE-RUN GREEN 2026-08-21**
   on the current build: in Simple, an append to an existing file produced the
   read card, then the edit card naming the file ("It wants to change the file
   “Notes.md”. You can undo this afterwards."), then the edit — per invocation,
   with undo. PR #101 surfaced both path-bounded file tools in SAFE; the
   folder-trust follow-up (owner decision 2026-08-12) is built and was seen
   live ("Folders Addison may work in" panel in Simple).
   `agent_core/tools/write_project_file.py` · `agent_core/tools/registry.py` · artifact §03

### P3 — quality

7. ~~**Message segments fuse without whitespace** ("for you.The answer is…").~~
   **RE-RUN GREEN 2026-08-21.** Zero fused joints across the whole day —
   ~12 remote-driven turns plus the owner's own session, streaming and settled
   both. (PR #102. The one fused line seen all day was `echo >>` onto a file
   with no trailing newline — the shell writing exactly what an approved
   command said, out of scope by design.)
   Frontend message renderer · artifact §04

8. ~~**Appends drop the trailing newline.**~~ **RE-RUN GREEN 2026-08-21.**
   `X0\n` + "add a line X1" landed as `X0\nX1\n` — the shell restored the
   dropped byte (`needs_trailing_newline`, PR #108), and the narrow rule held
   in the other direction: a file already ending without a newline was left
   exactly as sent. (The first re-run "failure" was a stale Aug-9 shell binary
   wearing the current frontend — see the artifact's first panel, which warned
   about precisely this.)
   File-edit tool append path · artifact §09

9. ~~**Changes entries carry no timestamp.**~~ **RE-RUN GREEN 2026-08-21.**
   Every row in the Code screen's Changes list shows name, edit count and time
   ("x.txt 16:54", "b.txt 2× 16:21"). (PR #103.)
   Code screen Changes list · artifact §09

10. ~~**Revert confirm contradicts enforcement on swapped files.**~~ **RE-RUN
    GREEN 2026-08-21.** With a hard link planted at `a.txt`: selecting the
    change already shows "A different file is at that name now, so this isn't
    the change Addison made", the confirm makes no false "you've changed this
    file" claim, and pressing through refuses with "…so Addison won't put the
    old text there. Nothing was changed." — nothing written through the link.
    (PR #108.)
    Code screen revert confirm · artifact §10

11. ~~**Routine plan capture is conversation-scoped.**~~ **RE-RUN GREEN
    2026-08-21.** In a conversation carrying earlier read/edit tool calls, a
    calculation's "Save as routine" offered exactly one step ("1. Do math and
    unit conversions"). (PR #104.)
    Routine plan capture · artifact §06

12. ~~**"Technical details" adds nothing.**~~ **RE-RUN GREEN 2026-08-21**
    (owner-driven, Wi-Fi off): the fold showed `provider: google ·
    gemma-4-31b-it` and `ProviderUnavailable(…)` — the provider and attempted
    model id, which the sentence did not carry. No status code, honestly: no
    HTTP happened. (PR #108.) The same screenshot is now the sharpest evidence
    on the cost-first-vs-explicit-pick open question below.
    Error surface · artifact §02

13. ~~**Status footer claims before it knows.**~~ **RE-RUN GREEN 2026-08-21**,
    two launches: the footer stays empty until the engine answers, then states
    the real profile. No premature "Simple profile · local". (PR #105.)
    Frontend status bar · artifact §01

14. ~~**One thing, two names.**~~ **RE-RUN GREEN 2026-08-21.** Sidebar row and
    Settings section both read "Restore points"; "snapshot" stays internal
    vocabulary (naming decision recorded in
    `docs/design-brief-dark/IMPLEMENTATION.md`). (PR #106.)
    Naming, sidebar + SettingsPage.

15. ~~**Mermaid diagrams render unthemed and clipped.**~~ **RE-RUN GREEN
    2026-08-21 in the light theme** — themed node fills, legible dark-on-light
    text, labels wrap without truncation, arrows are thin lines with proper
    heads (PR #107). The dark-theme glance is still to be taken; light was the
    red-railed observation.
    Frontend mermaid renderer / theme wiring · artifact §04

### Open questions (need a decision or one more observation, not yet a defect)

- **Cost-first vs. explicit model pick — evidence and an owner directive,
  2026-08-21.** With Claude Haiku 4.5 explicitly picked and strategy Cost
  first, an offline turn's Technical details showed the router attempting
  `provider: google · gemma-4-31b-it`: the explicit pick does not win, and
  nothing in the UI says which model actually answers. **Owner note
  (2026-08-21): the picker system reads as broken when this happens; the
  answering model should be disclosed on a tab/indicator next to the model
  picker.** Design work for a next wave; not a re-run item. artifact §02
- **Free-model disclaimer:** ~~gemini-3.5-flash on a free-tier key answered with
  no "answered with a free model" note.~~ **DECIDED AND FIXED 2026-08-15** (PR
  #113 part B): the chip now asks only `free`, known-free stays by
  construction (Ollama locals; cloud entries stay `false`).
- ~~**A routine's answer never reaches the person.**~~
  **DECIDED 2026-08-12 (owner).** A run now says what it is doing while it does
  it, and hands back what it produced. The engine emits one `routine.stepUpdate`
  per step as it begins and again when it ends (tool IDS — the RPC layer labels
  them from the registry, so a routine's steps read exactly like the chat panel's);
  `routine.run` carries `answer`, the last text the run produced. The Settings
  routine row expands a small panel under itself in the "Addison's work" idiom —
  live steps, a step waiting on a permission card saying so, a failed step naming
  itself in a plain sentence, and the answer as readable text at the end.
- **Diagnostics entries seemed profile-scoped** — confirm and decide. artifact §01
- **The work panel double-lists a step while the turn streams (new,
  2026-08-21).** One `calculator` call produced two identical "Do math and unit
  conversions" bullets live; the same panel after reload shows one. One audit
  row, so this is the streaming renderer, not dispatch. Cosmetic; worth a look
  the next time the work panel is open anyway. Frontend work-panel streaming.
  The 2026-09-29 hunt found the cause, which is now entry 62.
