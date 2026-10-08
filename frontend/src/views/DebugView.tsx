import DebugPane from "../debug/DebugPane";
import { useDebug } from "../debug/debugContext";

export default function DebugView() {
  const { enabled } = useDebug();
  return (
    <section>
      <h1>Debug</h1>
      {enabled ? (
        <DebugPane variant="page" />
      ) : (
        <p className="status">
          Debug mode is off. Start the backend with LLMLL_DEBUG=true.
        </p>
      )}
    </section>
  );
}
