import { useLayoutEffect, useRef } from "react";

const UMLAUTS = ["ä", "ö", "ü", "ß", "Ä", "Ö", "Ü"];

/**
 * German text field (single line or multiline) with umlaut buttons. Insertion is caret-safe:
 * the caret is restored in useLayoutEffect after React re-renders the controlled field, so a
 * second umlaut tapped right away lands in the right place.
 */
export default function UmlautField({
  value,
  onChange,
  locked,
  multiline = false,
  label = "Your answer",
}: {
  value: string;
  onChange: (value: string) => void;
  locked: boolean;
  multiline?: boolean;
  label?: string;
}) {
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const caretRef = useRef<number | null>(null);

  function insert(ch: string) {
    const el = ref.current;
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    caretRef.current = start + ch.length;
    onChange(value.slice(0, start) + ch + value.slice(end));
  }

  useLayoutEffect(() => {
    const el = ref.current;
    const caret = caretRef.current;
    if (el && caret !== null) {
      el.focus();
      el.setSelectionRange(caret, caret);
      caretRef.current = null;
    }
  }, [value]);

  const common = {
    ref,
    className: "text-input",
    "aria-label": label,
    lang: "de",
    autoCapitalize: "off",
    autoCorrect: "off",
    autoComplete: "off",
    spellCheck: false,
    value,
    readOnly: locked,
    onChange: (e: { target: { value: string } }) => onChange(e.target.value),
  };

  return (
    <>
      <div className="umlauts">
        {UMLAUTS.map((ch) => (
          <button
            key={ch}
            type="button"
            className="btn key"
            disabled={locked}
            onClick={() => insert(ch)}
            aria-label={`Insert ${ch}`}
          >
            {ch}
          </button>
        ))}
      </div>
      {multiline ? (
        <textarea {...common} rows={4} className="text-input multiline" />
      ) : (
        <input {...common} type="text" />
      )}
    </>
  );
}
