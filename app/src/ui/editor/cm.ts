// CodeMirror 6 for part scripts: Python, line numbers, history, bracket matching, the drafting
// theme (greys only, from the CSS tokens, so both themes follow) and a rule on each `# mises-op`
// line so the user's feature ops stand out in the script. Mod-s saves.
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { bracketMatching, HighlightStyle, indentOnInput, syntaxHighlighting } from "@codemirror/language";
import { EditorState, RangeSetBuilder, type Extension } from "@codemirror/state";
import {
  Decoration, drawSelection, EditorView, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers, ViewPlugin,
  type DecorationSet, type ViewUpdate,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

const theme = EditorView.theme({
  "&": { height: "100%", color: "var(--ink)", backgroundColor: "var(--paper)", fontSize: "var(--fs)" },
  ".cm-scroller": { fontFamily: "var(--font-mono)", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--ink)", padding: "6px 0" },
  ".cm-gutters": { backgroundColor: "var(--paper)", color: "var(--faint)", borderRight: "1px solid var(--line)" },
  ".cm-activeLine": { backgroundColor: "var(--paper2)" },
  ".cm-activeLineGutter": { backgroundColor: "var(--paper2)", color: "var(--dim)" },
  "&.cm-focused": { outline: "none" },
  "&.cm-focused .cm-cursor": { borderLeftColor: "var(--ink)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": { backgroundColor: "var(--line2) !important" },
  ".cm-matchingBracket": { outline: "1px solid var(--dim)", backgroundColor: "transparent" },
  ".cm-mises-op": { boxShadow: "inset 2px 0 0 var(--dim)", backgroundColor: "var(--paper2)" },
});

const highlight = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword], fontWeight: "700" },
  { tag: [t.comment, t.lineComment], color: "var(--faint)", fontStyle: "italic" },
  { tag: [t.string, t.special(t.string)], color: "var(--dim)" },
  { tag: [t.number, t.bool, t.null], color: "var(--ink)", textDecoration: "underline dotted var(--faint)" },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: "var(--ink)", fontWeight: "600" },
  { tag: [t.definition(t.variableName)], color: "var(--ink)" },
  { tag: [t.operator, t.punctuation, t.bracket], color: "var(--dim)" },
]);

const opLine = Decoration.line({ class: "cm-mises-op" });

function opLines(view: EditorView): DecorationSet {
  const b = new RangeSetBuilder<Decoration>();
  for (const { from, to } of view.visibleRanges) {
    for (let pos = from; pos <= to;) {
      const line = view.state.doc.lineAt(pos);
      if (line.text.startsWith("# mises-op")) b.add(line.from, line.from, opLine);
      pos = line.to + 1;
    }
  }
  return b.finish();
}

const opRule = ViewPlugin.fromClass(class {
  decorations: DecorationSet;
  constructor(view: EditorView) { this.decorations = opLines(view); }
  update(u: ViewUpdate) { if (u.docChanged || u.viewportChanged) this.decorations = opLines(u.view); }
}, { decorations: (v) => v.decorations });

export interface EditorHooks { onChange(doc: string): void; onSave(): void }

export function editorExtensions(h: EditorHooks): Extension[] {
  return [
    lineNumbers(), highlightActiveLineGutter(), history(), drawSelection(), indentOnInput(), bracketMatching(), highlightActiveLine(),
    python(), syntaxHighlighting(highlight), theme, opRule,
    EditorState.tabSize.of(4),
    keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { h.onSave(); return true; } }, indentWithTab, ...defaultKeymap, ...historyKeymap]),
    EditorView.updateListener.of((u) => { if (u.docChanged) h.onChange(u.state.doc.toString()); }),
    EditorView.contentAttributes.of({ "aria-label": "Part script (Python)", spellcheck: "false" }),
  ];
}

export function createEditor(parent: HTMLElement, doc: string, h: EditorHooks): EditorView {
  return new EditorView({ parent, state: EditorState.create({ doc, extensions: editorExtensions(h) }) });
}

/** Replace the whole document (a reload), keeping it out of the undo history's way. */
export function setDoc(view: EditorView, doc: string): void {
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: doc } });
}
