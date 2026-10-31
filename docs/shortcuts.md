# Keyboard shortcuts

The app's shortcuts in one place. The source of truth is `app/src/lib/shortcuts.ts`;
`app/src/lib/shortcuts.test.ts` fails if a shortcut is added in code without being listed there
and here. ⌘ shortcuts work everywhere except while a dialog is open; single keys work only where
noted (the viewer or the parts list focused, a focused approval card).

## Everywhere

| Keys | Action |
|---|---|
| ⌘0 | Home (all builds) |
| ⌘1 | Design page |
| ⌘2 | Studies page |
| ⌘3 | Drawing page |
| ⌘4 | Print page |
| ⌘5 | Onshape page |
| ⌘6 | Parts directory page |
| ⌘7 | Parts list (BOM) page |
| ⌘K | Focus the command line |
| ⌘N | New agent run |
| ⇧⌘A | Agent manager |
| ⇧⌘. | Stop all runs |

## Design page

| Keys | Action |
|---|---|
| F | Fit the assembly in the view (or double-click empty space) |
| Esc | Back to the assembly from an open part / close Play |
| M | Measure (viewer focused; again for Select) |
| Esc | Back to Select / close the overview, a menu or an immersive view |

The viewer's display options (shaded / wireframe, this part / whole build, Measure, Comment,
X-ray, Section, Explode, Reference image, Background, Add a feature, Part script) are in its
View ▾ menu: ↓ / ↑ open it, ↑ ↓ Home End move, Enter / Space pick, Esc closes.

## Parts list (left pane, a part focused)

| Keys | Action |
|---|---|
| F2 | Rename the part |
| H | Hide / show the selected parts |
| ⌘D | Duplicate the selected parts |
| ⌘I | Part properties |
| ⌫ | Delete the selected parts (and their assembly instances) |
| ⌥↑ ⌥↓ | Move the part up / down |
| ⌥→ ⌥← | Nest the part / move it out |

## Approval cards (a card focused, after it arms)

| Keys | Action |
|---|---|
| M | Approve (a focused approval card, after it arms) |
| R | Reject (a focused approval card) |
| K | Keep the branch (a focused approval card) |

## Immersive views

| Keys | Action |
|---|---|
| ⌘[ ⌘] | Previous / next view or study (immersive) |
