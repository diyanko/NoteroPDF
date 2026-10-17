# Product direction

NoteroPDF's long-term direction is a free, open-source companion for academic
workflows: papers, supporting documents, notes, annotations, and their source
references. Keep each capability small, reliable, and easy to set up. The released
application currently supports PDFs from Zotero matched to Notero-created Notion pages; this roadmap
describes direction, not promised features or release dates.

## Priorities

1. **Make the first successful sync easy.** Keep installation, connection,
   preview, confirmation, and recovery understandable to someone who does not
   develop software. Check the instructions against the published package.
   Explain Notion account and permission requirements early. Evaluate simpler
   supported login when it reduces setup friction without distributing secrets.
   Keep output readable without color and the CLI available across platforms.
2. **Improve the CLI in small steps.** Finish and validate useful increments,
   including library selection, saved connections, focused batches, clearer
   previews, explicit PDF choices, and supporting documents. Protect existing
   connections and data as capabilities grow.
3. **Develop the notes workflow.** Start with one real research journey and
   identify a useful gap in the existing Zotero-to-Notero-to-Notion workflow.
   Explore preserving annotation references, note structure, images, and links
   to source papers. Preview imported content and preserve user edits. Prefer
   one-way import with clear ownership before considering more complex sync.
4. **Consider files outside Zotero.** Explore explicit local-file selection and
   an explicit Notion destination as a separate workflow. Establish duplicate,
   ownership, and update behavior before implementing uploads.
5. **Consider a small graphical interface.** A Mac menu-bar app is one option.
   Decide after a concrete user need emerges; reuse the CLI's core operations
   and safety checks while keeping the cross-platform CLI available.

## Easier macOS installation

Consider a maintained Homebrew tap after the current CLI is integrated and its
first-run journey is verified. Keep PyPI and pipx as the cross-platform install
path. Follow [Homebrew's Python formula guidance](https://docs.brew.sh/Language-Specific-Formulae#python):
use `virtualenv_install_with_resources`, declare the Python runtime, and include
all transitive Python dependencies as immutable resources with verified checksums.
Test useful installed behavior in an isolated environment, including a preview
that cannot write, rather than checking only that the executable starts.
A formula, tap, and publishing automation remain future work.

## How work moves forward

Use GitHub issues for substantial ideas and reproducible bugs. Choose the smallest
useful outcome, implement it, verify it, and update the relevant documentation.
Prefer finishing one change over growing a stack of dependent feature branches.
An experiment is not a released capability until it is integrated and validated.

Stay local-first and avoid requiring a hosted service, paid account infrastructure,
or a growing set of dependencies to use the core tool. Preserve read-only Zotero
access, secure credentials, preview approval, and protection for unknown or
concurrently changed Notion files in every new interface.

## Public presentation

The README and PyPI description should explain the released product, requirements,
install command, and first successful run. Keep their installation instructions
consistent. Release notes should explain user-visible improvements, compatibility
changes, and any action existing users need to take.

Demonstrations and announcements should use a clean installation and synthetic
data, show preview and confirmation, and link to the published package and user
guide. Describe planned capabilities as plans. Keep promotion accurate as the
product expands, and retain NoteroPDF's name until there is a concrete reason
to change it.

## Interoperability and future tools

Keep one application and repository while the capabilities serve the same
workflow. Extract a companion tool only when it has a clear independent audience
or substantially different requirements. Share stable core operations when that
need arises; do not build a general platform in anticipation.

Use the supported Notion REST API for predictable operations. Evaluate
[Notion's Markdown content API](https://developers.notion.com/guides/data-apis/working-with-markdown-content)
for note workflows. The official [Notion CLI (`ntn`)](https://developers.notion.com/cli/get-started/overview)
and [Notion MCP](https://developers.notion.com/guides/mcp/overview) can support
future integrations, but are optional candidates, not required dependencies.
Choose interfaces based on reliability, permissions, fidelity, and maintenance
cost. Keep the core usable without a paid AI subscription.

[Notero already synchronizes notes and extracted PDF annotations](https://github.com/dvanoni/notero#syncing-notes-and-pdf-annotations).
Build on that workflow and target demonstrated gaps. Where appropriate, support
portable formats such as Markdown with source links, while checking that research
content and citations survive conversion. Notion and Zotero account limits still
apply; state them clearly rather than promising unrestricted access on every plan.
