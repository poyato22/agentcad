// Full-width page scaffold used by the Studies / Drawing / Print / Onshape / Parts pages. It renders a page
// header with the title and a scrolling body. The page sits under the top bar, so its header is
// already clear of the macOS traffic lights (which only cover the top-left of the window).
// Tasks 6–8 keep this wrapper and fill `children`.
import type { ComponentChildren, JSX } from "preact";
import "./pages.css";

export function PageShell({ title, children }: { title: string; children?: ComponentChildren }): JSX.Element {
  return (
    <section class="page" data-page-shell aria-label={title}>
      <header class="page-head">
        <h1 class="page-title">{title}</h1>
      </header>
      <div class="page-body">{children}</div>
    </section>
  );
}

/** A placeholder body the stub pages share until their real content lands. */
export function ComingNext({ what }: { what: string }): JSX.Element {
  return <p class="page-coming faint">{what} — coming next.</p>;
}
