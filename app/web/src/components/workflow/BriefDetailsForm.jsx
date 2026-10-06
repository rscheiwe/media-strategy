import { useMemo, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { count, money } from "@/lib/workflow";

/**
 * The clarification form (a FORM_REQUIRED tool output): button groups for the enum details,
 * a budget input with presets, client and impression-goal fields, all pre-filled with what
 * the brief already said. Only the open form is interactive; an answered or superseded form
 * renders read-only with its values. Submitting sends the values as structured metadata, so
 * the workflow runs on them directly (no model re-reads the answer).
 */
export function BriefDetailsForm({ form, interactive, submittedValues, busy, onSubmit }) {
  const config = form.form_config || {};
  const fields = useMemo(() => config.fields || [], [config.fields]);
  const initial = useMemo(() => {
    const values = {};
    for (const field of fields) values[field.name] = field.value ?? "";
    return { ...values, ...(submittedValues || {}) };
  }, [fields, submittedValues]);
  const [values, setValues] = useState(initial);
  const readOnly = !interactive;

  const set = (name, value) => setValues((prev) => ({ ...prev, [name]: value }));
  const requiredOk = fields
    .filter((field) => field.required)
    .every((field) => {
      const value = values[field.name];
      return field.type === "number" ? Number(value) > 0 : Boolean(value);
    });

  const submit = (event) => {
    event.preventDefault();
    if (readOnly || !requiredOk || busy) return;
    const parameters = {};
    for (const field of fields) {
      const value = values[field.name];
      if (value === "" || value === null || value === undefined) parameters[field.name] = null;
      else parameters[field.name] = field.type === "number" ? Number(value) : value;
    }
    onSubmit?.(form.form_id, parameters);
  };

  return (
    <form
      onSubmit={submit}
      aria-label={config.title || "Brief details"}
      className={cn(
        "my-3 rounded-xl border border-border bg-card p-4 shadow-sm",
        readOnly && "opacity-80",
      )}
    >
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{config.title || "Brief details"}</h3>
          {config.description ? (
            <p className="mt-0.5 text-[13px] text-muted-foreground">{config.description}</p>
          ) : null}
        </div>
        {readOnly ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground">
            <Check className="size-3" /> {submittedValues ? "Submitted" : "Closed"}
          </span>
        ) : null}
      </div>

      <div className="space-y-3">
        {fields.map((field) => (
          <FormField
            key={field.name}
            field={field}
            value={values[field.name]}
            readOnly={readOnly}
            onChange={(value) => set(field.name, value)}
          />
        ))}
      </div>

      {!readOnly ? (
        <div className="mt-4 flex justify-end">
          <Button type="submit" size="sm" disabled={!requiredOk || busy}>
            {config.submit_label || "Submit"}
          </Button>
        </div>
      ) : null}
    </form>
  );
}

function FieldLabel({ field }) {
  return (
    <div className="mb-1.5 flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground">
      <span>
        {field.label}
        {field.required ? <span className="text-destructive"> *</span> : null}
      </span>
      {field.missing ? (
        <span className="rounded bg-amber-100 px-1.5 py-px text-[10px] font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
          needed
        </span>
      ) : null}
      {field.inferred && !field.missing ? (
        <span className="rounded bg-sky-100 px-1.5 py-px text-[10px] font-semibold text-sky-800 dark:bg-sky-900/40 dark:text-sky-300">
          confirm
        </span>
      ) : null}
    </div>
  );
}

function FormField({ field, value, readOnly, onChange }) {
  const id = `wf-field-${field.name}`;
  if (field.type === "choice") {
    return (
      <div role="group" aria-label={field.label}>
        <FieldLabel field={field} />
        <div className="flex flex-wrap gap-1.5">
          {(field.options || []).map((option) => {
            const selected = value === option.value;
            return (
              <Button
                key={option.value}
                type="button"
                size="sm"
                variant={selected ? "default" : "outline"}
                aria-pressed={selected}
                disabled={readOnly}
                onClick={() => onChange(option.value)}
              >
                {option.label}
              </Button>
            );
          })}
        </div>
      </div>
    );
  }
  if (field.type === "number") {
    return (
      <div>
        <label htmlFor={id}>
          <FieldLabel field={field} />
        </label>
        <div className="flex flex-wrap items-center gap-1.5">
          <Input
            id={id}
            type="number"
            inputMode="numeric"
            min={field.min ?? 0}
            value={value ?? ""}
            disabled={readOnly}
            onChange={(event) => onChange(event.target.value)}
            className="h-8 w-40"
          />
          {(field.presets || []).map((preset) => (
            <Button
              key={preset}
              type="button"
              size="xs"
              variant={Number(value) === preset ? "secondary" : "ghost"}
              disabled={readOnly}
              onClick={() => onChange(preset)}
            >
              {field.name === "budget" ? money(preset) : count(preset)}
            </Button>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div>
      <label htmlFor={id}>
        <FieldLabel field={field} />
      </label>
      {field.suggestions?.length ? (
        <SuggestInput
          id={id}
          value={value ?? ""}
          placeholder={field.placeholder}
          suggestions={field.suggestions}
          disabled={readOnly}
          onChange={onChange}
        />
      ) : (
        <Input
          id={id}
          type="text"
          value={value ?? ""}
          placeholder={field.placeholder}
          disabled={readOnly}
          onChange={(event) => onChange(event.target.value)}
          className="h-8 max-w-sm"
        />
      )}
    </div>
  );
}

/**
 * A text input with a suggestion list (the sample briefs' advertisers): type any name, or pick
 * one. The list sits under the input at its width, filters as you type, and works with the
 * keyboard (Up/Down, Enter, Escape). A custom list rather than <datalist>, whose popup the
 * browser positions and styles.
 */
function SuggestInput({ id, value, placeholder, suggestions, disabled, onChange }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const query = String(value || "")
    .trim()
    .toLowerCase();
  const matches = suggestions.filter((name) => !query || name.toLowerCase().includes(query));
  const listId = `${id}-suggestions`;
  const show =
    open && !disabled && matches.length > 0 && !(matches.length === 1 && matches[0] === value);

  const pick = (name) => {
    onChange(name);
    setOpen(false);
    setActive(-1);
  };
  const onKeyDown = (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((i) => Math.min(i + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter" && show && active >= 0) {
      event.preventDefault(); // pick the highlighted name, don't submit the form
      pick(matches[active]);
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div className="relative max-w-sm">
      <Input
        id={id}
        type="text"
        role="combobox"
        aria-expanded={show}
        aria-controls={listId}
        aria-autocomplete="list"
        autoComplete="off"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(event) => {
          onChange(event.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKeyDown}
        className="h-8 pr-8"
      />
      <ChevronDown
        aria-hidden
        className="pointer-events-none absolute right-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
      />
      {show ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 top-full z-20 mt-1 overflow-hidden rounded-md border border-border bg-popover py-1 text-[13px] shadow-md"
        >
          {matches.map((name, i) => (
            <li
              key={name}
              role="option"
              aria-selected={i === active}
              // mousedown, not click: picks before the input's blur closes the list
              onMouseDown={(event) => {
                event.preventDefault();
                pick(name);
              }}
              onMouseEnter={() => setActive(i)}
              className={cn(
                "cursor-pointer px-2.5 py-1.5",
                i === active && "bg-accent text-accent-foreground",
              )}
            >
              {name}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
