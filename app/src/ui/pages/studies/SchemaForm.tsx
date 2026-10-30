// The settings form, generated from a study type's JSON schema (GET /sims/types): numbers with
// their unit and range, choices, switches, nullable fields ("auto"), directions (a name or a
// vector), faces (preset, face id, or picked in the viewer), lists (load cases, loads, supports,
// thermal faces, yaw angles) and tagged unions (the load kind). Settings in ADVANCED fold under
// "Advanced". Problems show at their field. Every label and doc comes from the schema as text.
import type { JSX } from "preact";
import { useRef, useState } from "preact/hooks";
import type { JsonSchema } from "../../../api/studies";
import type { PartFace } from "../../../api/types";
import { fmtClock } from "../../../lib/format";
import { Button } from "../../common/Button";
import { unitText } from "./model";
import {
  ADVANCED, defaultFor, deref, directionNames, discriminated, FACE_PRESETS, getAt, isDirection, isFaceKey, labelFor, nullableInner,
  pathKey, properties, setAt, type FieldError, type Path,
} from "./formSchema";

export interface SchemaFormProps {
  schema: JsonSchema;
  value: Record<string, unknown>;
  onChange: (v: Record<string, unknown>) => void;
  errors?: readonly FieldError[];
  disabled?: boolean;
  /** The part's faces (face ids for the face pickers). */
  faces?: readonly PartFace[];
  /** Material names the material field offers (built-in + saved). */
  materials?: readonly string[];
  onEditMaterials?: () => void;
  /** Arms the viewer to pick a face for this field; null disarms. */
  onPickFace?: (path: Path | null) => void;
  picking?: Path | null;
  /** The load case shown selected in the viewer (static: index into load_cases). */
  selectedCase?: number | null;
  onSelectCase?: (i: number) => void;
}

interface Ctx extends SchemaFormProps { root: JsonSchema; errs: Map<string, string[]>; set: (p: Path, v: unknown) => void }

let nextRowKey = 0;
const ENVIRONMENTS = ["Desk", "Outdoor", "Bike mount", "Car dashboard 80°C"];
const fid = (p: Path) => `sf-${p.map(String).join("-").replace(/[^a-zA-Z0-9_-]/g, "_")}`;

export function SchemaForm(props: SchemaFormProps): JSX.Element {
  const [adv, setAdv] = useState(false);
  const root = props.schema;
  const errs = new Map<string, string[]>();
  for (const e of props.errors ?? []) errs.set(e.path, [...(errs.get(e.path) ?? []), e.message]);
  const ctx: Ctx = { ...props, root, errs, set: (p, v) => props.onChange(setAt(props.value, p, v)) };
  const n = deref(root, root);
  const props_ = properties(n);
  const basic = props_.filter((p) => !ADVANCED.has(p.key));
  const advanced = props_.filter((p) => ADVANCED.has(p.key));
  const advErrors = advanced.some((p) => [...errs.keys()].some((k) => k === p.key || k.startsWith(`${p.key}.`)));
  return (
    <div class="sf" data-testid="schema-form">
      {basic.map((p) => <Field key={p.key} ctx={ctx} path={[p.key]} k={p.key} schema={p.schema} required={p.required} />)}
      {advanced.length > 0 && (
        <details class="sf-adv" open={adv || advErrors} onToggle={(e) => setAdv((e.currentTarget as HTMLDetailsElement).open)}>
          <summary class="label">Advanced{advErrors ? " · check these" : ""}</summary>
          <div class="sf-adv-body">
            {advanced.map((p) => <Field key={p.key} ctx={ctx} path={[p.key]} k={p.key} schema={p.schema} required={p.required} />)}
          </div>
        </details>
      )}
    </div>
  );
}

function Errors({ ctx, path }: { ctx: Ctx; path: Path }): JSX.Element | null {
  const list = ctx.errs.get(pathKey(path));
  if (!list?.length) return null;
  return <span class="sf-err" id={`${fid(path)}-err`} role="alert">{list.join("; ")}</span>;
}

function Doc({ schema }: { schema: JsonSchema }): JSX.Element | null {
  return typeof schema.description === "string" ? <span class="sf-doc faint text">{schema.description}</span> : null;
}

function rangeText(n: JsonSchema): string {
  const lo = typeof n.minimum === "number" ? `≥ ${n.minimum}` : typeof n.exclusiveMinimum === "number" ? `> ${n.exclusiveMinimum}` : "";
  const hi = typeof n.maximum === "number" ? `≤ ${n.maximum}` : typeof n.exclusiveMaximum === "number" ? `< ${n.exclusiveMaximum}` : "";
  return [lo, hi].filter(Boolean).join(", ");
}

function NumberInput({ ctx, path, n, value, label }: { ctx: Ctx; path: Path; n: JsonSchema; value: unknown; label?: string }): JSX.Element {
  const has = ctx.errs.has(pathKey(path));
  return (
    <input id={fid(path)} type="number" step={n.type === "integer" ? 1 : "any"} inputMode="decimal" disabled={ctx.disabled}
      value={typeof value === "number" && Number.isFinite(value) ? String(value) : ""} aria-label={label}
      aria-invalid={has ? "true" : undefined} aria-describedby={has ? `${fid(path)}-err` : undefined}
      min={typeof n.minimum === "number" ? n.minimum : undefined} max={typeof n.maximum === "number" ? n.maximum : undefined}
      onInput={(e) => {
        const t = (e.target as HTMLInputElement).value;
        const x = t.trim() === "" ? Number.NaN : n.type === "integer" ? Number.parseInt(t, 10) : Number.parseFloat(t);
        ctx.set(path, x);
      }} />
  );
}

function FaceInput({ ctx, path, value, label }: { ctx: Ctx; path: Path; value: unknown; label: string }): JSX.Element {
  const v = typeof value === "string" ? value : "";
  const ids = (ctx.faces ?? []).map((f) => f.id);
  const known = (FACE_PRESETS as readonly string[]).includes(v) || ids.includes(v);
  const armed = ctx.picking !== null && ctx.picking !== undefined && pathKey(ctx.picking) === pathKey(path);
  const face = (ctx.faces ?? []).find((f) => f.id === v);
  return (
    <span class="sf-face">
      <select id={fid(path)} value={v} disabled={ctx.disabled} aria-label={label} onChange={(e) => ctx.set(path, (e.target as HTMLSelectElement).value)}>
        <optgroup label="Sides">{FACE_PRESETS.map((f) => <option key={f} value={f}>{f}</option>)}</optgroup>
        {ids.length > 0 && <optgroup label="Faces">{(ctx.faces ?? []).map((f) => <option key={f.id} value={f.id}>{f.id} · {f.type} · {Math.round(f.area)} mm²</option>)}</optgroup>}
        {!known && v && <option value={v}>{v}</option>}
      </select>
      {ctx.onPickFace && (
        <Button variant={armed ? "primary" : "default"} aria-pressed={armed} disabled={ctx.disabled}
          aria-label={armed ? `Picking a face for ${label}: click the part, or press Escape` : `Pick the face for ${label} in the viewer`}
          onClick={() => ctx.onPickFace?.(armed ? null : path)} data-testid="pick-face">{armed ? "Click a face…" : "Pick"}</Button>
      )}
      {face && <span class="faint sf-face-info">{face.type}{face.normal ? ` · n ${face.normal.map((x) => Math.round(x * 100) / 100).join(", ")}` : ""}</span>}
    </span>
  );
}

function DirectionInput({ ctx, path, n, value, label }: { ctx: Ctx; path: Path; n: JsonSchema; value: unknown; label: string }): JSX.Element {
  const names = directionNames(n);
  const isVec = Array.isArray(value);
  const vec = isVec ? (value as unknown[]).map((x) => (typeof x === "number" ? x : 0)) : [0, 0, -1];
  return (
    <span class="sf-dir">
      <select id={fid(path)} value={isVec ? "vector" : String(value ?? names[0] ?? "-z")} disabled={ctx.disabled} aria-label={label}
        onChange={(e) => { const t = (e.target as HTMLSelectElement).value; ctx.set(path, t === "vector" ? vec : t); }}>
        {names.map((d) => <option key={d} value={d}>{d.replace("-", "−")}</option>)}
        <option value="vector">vector…</option>
      </select>
      {isVec && ["x", "y", "z"].map((ax, i) => (
        <input key={ax} type="number" step="any" class="sf-vec" aria-label={`${label} ${ax}`} disabled={ctx.disabled} value={String(vec[i])}
          onInput={(e) => ctx.set([...path, i], Number.parseFloat((e.target as HTMLInputElement).value))} />
      ))}
    </span>
  );
}

function Row({ ctx, path, label, unit, n, children, inline = true }: { ctx: Ctx; path: Path; label: string; unit?: string; n: JsonSchema; children: JSX.Element; inline?: boolean }): JSX.Element {
  const range = rangeText(n);
  return (
    <div class={`sf-row${inline ? "" : " sf-row-block"}`} data-field={pathKey(path)}>
      <label class="sf-label" for={fid(path)}>{label}{unit ? <span class="faint"> {unitText(unit)}</span> : null}</label>
      <span class="sf-control">{children}{range && <span class="sf-range faint" aria-hidden="true">{range}</span>}</span>
      <Doc schema={n} />
      <Errors ctx={ctx} path={path} />
    </div>
  );
}

function Field({ ctx, path, k, schema, required }: { ctx: Ctx; path: Path; k: string | number; schema: JsonSchema; required: boolean }): JSX.Element | null {
  const root = ctx.root;
  const n = deref(schema, root);
  const value = getAt(ctx.value, path);
  const label = labelFor(k, n);
  const unit = typeof n.unit === "string" ? n.unit : undefined;
  if ("const" in n) return null; // a union's tag: the kind picker shows it

  // material name: the saved and built-in materials
  if (k === "name" && path.length === 2 && path[0] === "material") {
    const names = ctx.materials ?? [];
    return (
      <Row ctx={ctx} path={path} label="Material" n={{}}>
        <span class="sf-face">
          <select id={fid(path)} disabled={ctx.disabled} value={typeof value === "string" ? value : ""} onChange={(e) => { const t = (e.target as HTMLSelectElement).value; ctx.set(path, t || null); }}>
            <option value="">profile default</option>
            {names.map((m) => <option key={m} value={m}>{m}</option>)}
            {typeof value === "string" && value && !names.includes(value) && <option value={value}>{value}</option>}
          </select>
          {ctx.onEditMaterials && <Button variant="ghost" onClick={ctx.onEditMaterials} disabled={ctx.disabled}>Edit materials</Button>}
        </span>
      </Row>
    );
  }
  if (k === "environment") {
    return (
      <Row ctx={ctx} path={path} label="Environment" n={n}>
        <span>
          <input id={fid(path)} list="sf-envs" disabled={ctx.disabled} value={typeof value === "string" ? value : ""} placeholder="none"
            onInput={(e) => { const t = (e.target as HTMLInputElement).value; ctx.set(path, t.trim() ? t : null); }} />
          <datalist id="sf-envs">{ENVIRONMENTS.map((x) => <option key={x} value={x} />)}</datalist>
        </span>
      </Row>
    );
  }
  if (isFaceKey(k)) return <Row ctx={ctx} path={path} label={label} n={{ description: undefined }}><FaceInput ctx={ctx} path={path} value={value} label={label} /></Row>;
  if (isDirection(n)) return <Row ctx={ctx} path={path} label={label} n={{ ...n, description: n.description }}><DirectionInput ctx={ctx} path={path} n={n} value={value} label={label} /></Row>;

  const inner = nullableInner(n, root);
  if (inner) {
    const on = value !== null && value !== undefined;
    const simple = inner.type === "number" || inner.type === "integer" || inner.type === "string";
    const autoText = /null = ([^;,)]+)/.exec(String(n.description ?? ""))?.[1] ?? "off";
    if (simple) {
      return (
        <Row ctx={ctx} path={path} label={label} unit={unit ?? (typeof inner.unit === "string" ? inner.unit : undefined)} n={inner}>
          <span class="sf-nullable">
            <label class="sf-check"><input type="checkbox" checked={!on} disabled={ctx.disabled} aria-label={`${label}: ${autoText}`}
              onChange={() => ctx.set(path, on ? null : defaultFor({ ...inner, default: undefined }, root))} />{autoText}</label>
            {on && (inner.type === "string"
              ? <input id={fid(path)} disabled={ctx.disabled} value={String(value)} onInput={(e) => ctx.set(path, (e.target as HTMLInputElement).value)} />
              : <NumberInput ctx={ctx} path={path} n={inner} value={value} label={label} />)}
          </span>
        </Row>
      );
    }
    // a nullable object or tuple: a switch, then its fields
    return (
      <fieldset class="sf-group" data-field={pathKey(path)}>
        <legend><label class="sf-check"><input type="checkbox" checked={on} disabled={ctx.disabled}
          onChange={() => ctx.set(path, on ? null : defaultFor(inner, root, k))} /> {label}</label></legend>
        <Doc schema={n} />
        {on && <Inner ctx={ctx} path={path} k={k} n={inner} />}
        <Errors ctx={ctx} path={path} />
      </fieldset>
    );
  }
  return <Inner ctx={ctx} path={path} k={k} n={n} required={required} />;
}

function Inner({ ctx, path, k, n, required = true }: { ctx: Ctx; path: Path; k: string | number; n: JsonSchema; required?: boolean }): JSX.Element | null {
  const root = ctx.root;
  const value = getAt(ctx.value, path);
  const label = labelFor(k, n);
  const unit = typeof n.unit === "string" ? n.unit : undefined;
  void required;
  if (Array.isArray(n.enum)) {
    return (
      <Row ctx={ctx} path={path} label={label} n={n}>
        <select id={fid(path)} disabled={ctx.disabled} value={String(value ?? "")} onChange={(e) => ctx.set(path, (e.target as HTMLSelectElement).value)}>
          {(n.enum as unknown[]).map((x) => <option key={String(x)} value={String(x)}>{String(x).replace(/_/g, " ")}</option>)}
        </select>
      </Row>
    );
  }
  switch (n.type) {
    case "number": case "integer":
      return <Row ctx={ctx} path={path} label={label} unit={unit} n={n}><NumberInput ctx={ctx} path={path} n={n} value={value} label={label} /></Row>;
    case "boolean":
      return (
        <div class="sf-row" data-field={pathKey(path)}>
          <label class="sf-check"><input id={fid(path)} type="checkbox" checked={value === true} disabled={ctx.disabled} onChange={(e) => ctx.set(path, (e.target as HTMLInputElement).checked)} />{label}</label>
          <Doc schema={n} /><Errors ctx={ctx} path={path} />
        </div>
      );
    case "string":
      return (
        <Row ctx={ctx} path={path} label={label} n={n}>
          <input id={fid(path)} disabled={ctx.disabled} value={typeof value === "string" ? value : ""} maxLength={typeof n.maxLength === "number" ? n.maxLength : undefined}
            onInput={(e) => ctx.set(path, (e.target as HTMLInputElement).value)} />
        </Row>
      );
    case "object":
      return (
        <fieldset class="sf-group" data-field={pathKey(path)}>
          <legend>{label}</legend>
          {properties(n).map((p) => <Field key={p.key} ctx={ctx} path={[...path, p.key]} k={p.key} schema={p.schema} required={p.required} />)}
          <Errors ctx={ctx} path={path} />
        </fieldset>
      );
    case "array":
      if (Array.isArray(n.prefixItems)) {
        const items = n.prefixItems as JsonSchema[];
        const arr = Array.isArray(value) ? value : [];
        return (
          <Row ctx={ctx} path={path} label={label} unit={unit} n={n}>
            <span class="sf-tuple">
              {items.map((s, i) => <NumberInput key={i} ctx={ctx} path={[...path, i]} n={deref(s, root)} value={arr[i]} label={`${label} ${i === 0 ? "from" : i === 1 && items.length === 2 ? "to" : "xyz"[i]}`} />)}
            </span>
          </Row>
        );
      }
      return <ArrayField ctx={ctx} path={path} k={k} n={n} />;
    default:
      return null;
  }
}

function ArrayField({ ctx, path, k, n }: { ctx: Ctx; path: Path; k: string | number; n: JsonSchema }): JSX.Element {
  const root = ctx.root;
  const items = deref((n.items ?? {}) as JsonSchema, root);
  const arr = Array.isArray(getAt(ctx.value, path)) ? (getAt(ctx.value, path) as unknown[]) : [];
  const label = labelFor(k, n);
  const max = typeof n.maxItems === "number" ? n.maxItems : 100;
  const min = typeof n.minItems === "number" ? n.minItems : 0;
  const unit = typeof n.unit === "string" ? n.unit : undefined;
  // stable row keys: a moved or removed row keeps its own inputs (not its neighbour's)
  const keys = useRef<string[]>([]);
  const ks = keys.current;
  while (ks.length < arr.length) ks.push(`row${nextRowKey++}`);
  if (ks.length > arr.length) ks.length = arr.length;
  const removeAt = (i: number) => {
    ks.splice(i, 1);
    ctx.set(path, arr.filter((_, j) => j !== i));
  };
  // a list of numbers (yaw angles): one text field, comma separated
  if (items.type === "number" || items.type === "integer") {
    return (
      <Row ctx={ctx} path={path} label={label} unit={unit} n={n}>
        <input id={fid(path)} disabled={ctx.disabled} placeholder="e.g. -10, 0, 10" defaultValue={arr.join(", ")}
          onChange={(e) => {
            const t = (e.target as HTMLInputElement).value;
            ctx.set(path, t.split(/[,\s]+/).filter(Boolean).map(Number));
          }} />
      </Row>
    );
  }
  // a list of faces (refine faces)
  if (items.type === "string") {
    return (
      <fieldset class="sf-group" data-field={pathKey(path)}>
        <legend>{label}</legend>
        <Doc schema={n} />
        {arr.map((_, i) => (
          <div key={ks[i]} class="sf-item-row">
            <FaceInput ctx={ctx} path={[...path, i]} value={arr[i]} label={`${label} ${i + 1}`} />
            <Button variant="ghost" disabled={ctx.disabled} aria-label={`Remove ${label} ${i + 1}`} onClick={() => removeAt(i)}>Remove</Button>
            <Errors ctx={ctx} path={[...path, i]} />
          </div>
        ))}
        {arr.length < max && <Button disabled={ctx.disabled} onClick={() => ctx.set(path, [...arr, "top"])}>Add face</Button>}
      </fieldset>
    );
  }
  const d = discriminated(items, root);
  const isCases = k === "load_cases";
  const itemName = (x: unknown, i: number): string => {
    const o = (typeof x === "object" && x !== null ? x : {}) as Record<string, unknown>;
    if (typeof o.name === "string" && o.name) return o.name;
    if (d && typeof o[d.prop] === "string") return `${String(o[d.prop]).replace(/_/g, " ")}${typeof o.face === "string" ? ` on ${o.face}` : ""}`;
    if (typeof o.kind === "string") return `${o.kind}${typeof o.face === "string" ? ` on ${o.face}` : ""}`;
    if (typeof o.face === "string") return o.face;
    return `${label} ${i + 1}`;
  };
  const move = (i: number, by: -1 | 1) => {
    const j = i + by;
    if (j < 0 || j >= arr.length) return;
    const next = [...arr]; [next[i], next[j]] = [next[j], next[i]];
    [ks[i], ks[j]] = [ks[j], ks[i]];
    ctx.set(path, next);
    if (isCases && ctx.selectedCase === i) ctx.onSelectCase?.(j);
  };
  return (
    <fieldset class={`sf-group sf-list${isCases ? " sf-cases" : ""}`} data-field={pathKey(path)} data-testid={isCases ? "load-cases" : undefined}>
      <legend>{label}{arr.length ? <span class="faint"> · {arr.length}</span> : null}</legend>
      <Errors ctx={ctx} path={path} />
      {arr.map((x, i) => {
        const ipath = [...path, i];
        const selected = isCases && ctx.selectedCase === i;
        const opt = d ? d.options.find((o) => o.value === (x as Record<string, unknown>)?.[d.prop]) ?? d.options[0] : null;
        const schema = opt ? opt.schema : items;
        return (
          <div key={ks[i]} class="sf-item" data-selected={selected ? "true" : undefined} data-testid={isCases ? "load-case" : "list-item"}>
            <div class="sf-item-head">
              {isCases
                ? <button type="button" class="sf-item-name" aria-pressed={selected} onClick={() => ctx.onSelectCase?.(i)}>{itemName(x, i)}</button>
                : <span class="sf-item-name text">{itemName(x, i)}</span>}
              <span class="sf-item-tools">
                <button type="button" class="sf-tool" disabled={ctx.disabled || i === 0} aria-label={`Move ${itemName(x, i)} up`} onClick={() => move(i, -1)}>↑</button>
                <button type="button" class="sf-tool" disabled={ctx.disabled || i === arr.length - 1} aria-label={`Move ${itemName(x, i)} down`} onClick={() => move(i, 1)}>↓</button>
                <button type="button" class="sf-tool" disabled={ctx.disabled || arr.length <= min} aria-label={`Remove ${itemName(x, i)}`} onClick={() => {
                  removeAt(i);
                  // the selected case keeps pointing at the same case (or its neighbour when it was removed)
                  const sel = ctx.selectedCase;
                  if (isCases && typeof sel === "number" && sel >= i) ctx.onSelectCase?.(Math.max(0, sel === i ? Math.min(i, arr.length - 2) : sel - 1));
                }}>✕</button>
              </span>
            </div>
            {d && (
              <div class="sf-row">
                <label class="sf-label" for={fid([...ipath, d.prop])}>Kind</label>
                <select id={fid([...ipath, d.prop])} disabled={ctx.disabled} value={opt?.value ?? ""}
                  onChange={(e) => {
                    const t = (e.target as HTMLSelectElement).value;
                    const next = d.options.find((o) => o.value === t);
                    if (!next) return;
                    const fresh = defaultFor(next.schema, root) as Record<string, unknown>;
                    const old = (x ?? {}) as Record<string, unknown>;
                    // keep what the two kinds share (the face, the force)
                    for (const key of ["face", "newtons", "direction"]) if (key in fresh && key in old) fresh[key] = old[key];
                    fresh[d.prop] = t;
                    ctx.set(ipath, fresh);
                  }}>
                  {d.options.map((o) => <option key={o.value} value={o.value}>{o.value.replace(/_/g, " ")}</option>)}
                </select>
                <Errors ctx={ctx} path={[...ipath, d.prop]} />
              </div>
            )}
            {properties(schema).map((p) => <Field key={p.key} ctx={ctx} path={[...ipath, p.key]} k={p.key} schema={p.schema} required={p.required} />)}
          </div>
        );
      })}
      {arr.length < max && (
        <Button disabled={ctx.disabled} data-testid={isCases ? "add-load-case" : undefined} onClick={() => {
          const fresh = defaultFor(items, root) as unknown;
          if (isCases && typeof fresh === "object" && fresh !== null) {
            const names = new Set(arr.map((x) => (x as Record<string, unknown>)?.name));
            // the core wants unique names; never a counter: the start time names it until the user does
            let name = "new case";
            if (names.has(name)) name = `case from ${fmtClock(Date.now() / 1000)}`;
            if (names.has(name)) name = `case from ${new Date().toISOString().slice(11, 23)}`;
            (fresh as Record<string, unknown>).name = name;
          }
          ctx.set(path, [...arr, fresh]);
          if (isCases) ctx.onSelectCase?.(arr.length);
        }}>{isCases ? "Add load case" : `Add ${label.toLowerCase().replace(/s$/, "")}`}</Button>
      )}
    </fieldset>
  );
}
