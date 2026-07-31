# Pipeline Studio — open, clone, and remove a saved recipe

## Context

Pipeline Studio could create a recipe and save it. It could not reopen one.
`load_pipeline_recipe` has existed in `musubi-data` since the Studio shipped,
`loadPipelineRecipe` has been wired through `TauriSource` to
`vals.pipelineBuilder.actions.onLoad`, and **nothing in `Pipeline.jsx` ever
rendered a control that called it**. The three recipes checked into
`.github/pipelines/` — `code-review`, `dev-lite`, `feature-dev` — were therefore
unreachable from the Studio, which is what made them look like fixed presets
rather than editable recipes.

Wiring an Open control alone would have been actively destructive. The Studio's
model carries six keys (`name`, `description`, `version`, `baseline_checks`,
`stages`, `correction`); `render_pipeline_recipe` was a bare
`serde_yaml::to_string` over exactly those. `code-review/pipeline.yaml` carries
three more — `level`, `max_credits: 20` (the credit budget), `warn_at` — which
`read_pipeline_recipe` discards on purpose (`let _legacy_ignored = …`), plus the
`# musubi-tier: / expires-when: / cost-lever:` header block that Hard Invariant
#9 requires of every component. Open-then-Save would have silently deleted all
six lines.

## Goal

Open a saved recipe and update it in place without losing anything the Studio
does not model; clone one; remove one that the Studio itself created. Keep the
repository's own recipes editable but undeletable.

## Tech stack

`musubi-data` (Rust, no Tauri toolchain needed), the Tauri command layer in
`gui/src-tauri/src/lib.rs`, `TauriSource.js`, `viewModel.js`, `Pipeline.jsx`.

## Steps

1. **Preserve what the model does not own.** `preserved_pipeline_prelude` reads
   the file about to be overwritten and returns its leading comment block plus
   every top-level key outside `STUDIO_OWNED_PIPELINE_KEYS`.
   `render_pipeline_recipe` re-emits both around the rendered document. A save
   under a *new* name finds no file and starts clean, which is what makes a
   clone a new recipe rather than a second copy of the original's governance
   tag and credit budget.

   `generator`/`evaluator` are on the owned list despite having no model field:
   they are the legacy stage shape, `read_pipeline_recipe` already folds them
   into `stages`, and preserving them beside the `stages:` the Studio writes
   would leave two contradictory stage lists in one file. Updating a
   legacy-shaped recipe therefore does convert it to the modern shape — the one
   deliberate rewrite.

2. **Mark which recipes are the repository's.** `pipeline_is_protected` reports
   whether the leading comment block carries a `musubi-tier:` tag. Because the
   renderer emits no comments, a Studio-minted recipe can never carry one — so
   the tag is an exact marker for "checked in and hand-authored", needing no git
   dependency, no schema change, and no hard-coded name list. Surfaced as
   `PipelineCatalogEntry.protected`.

3. **`delete_pipeline_recipe`.** Fail-closed in the same shape as the rest of
   the pipeline surface: an unsafe name, a missing recipe, or a tagged recipe
   returns an error and touches no disk. Otherwise `remove_dir_all` on the
   directory `checked_pipeline_path` has already canonically verified, then a
   catalog re-read to confirm.

4. **Clone is a local rename, not a write.** It mints an unused name
   (`<stem>-copy`, then `-copy-2`…, with an existing `-copy` suffix stripped so
   they do not stack) and clears `savedRecipe`, so the draft reads Unsaved and
   the existing validated Save path creates the new directory. Nothing reaches
   disk until Save, and the recipe cloned *from* is never the save target.

5. **UI.** An Open select listing saved recipes (repository-owned ones tagged
   `· repository`), Clone, and Remove. Remove confirms via `window.confirm` —
   the pattern the Orchestrator's Clean all already uses for a destructive bulk
   action — and is disabled with a reason on tagged recipes, so the refusal is
   visible before the click rather than as an error after it.

## Result

Updating `code-review` from the Studio now round-trips its tier block,
`max_credits`, `warn_at`, and `level`; before this change all four were dropped.
Four new `musubi-data` tests cover preservation, clean-clone, delete refusal,
and the catalog flag; six new console tests cover the view-model gate and the
clone/delete source actions.

## Not covered

`gui/src-tauri/src/lib.rs` is not compiled by CI — the `Rust console core` job
builds `gui/src-tauri/musubi-data` only, because the Tauri crate needs GTK and
webkit. The command wrapper and its handler registration are therefore checked
by the string assertions in `orchestrator_pipeline_ipc_and_active_routes_…`
rather than by a compiler. Building the desktop bundle is what would prove them.
