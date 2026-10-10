import { useCallback } from "react";
import { useParams } from "react-router";
import { getGrammar } from "../api/client";
import { useApi } from "../useApi";
import SessionView from "./SessionView";

/** Grammar drill: a mini-session of exercises on one grammar point. */
export default function DrillView() {
  const { id = "" } = useParams();
  const load = useCallback(() => getGrammar(id), [id]);
  const state = useApi(id, load);

  if (state.status === "loading") return <p role="status">Loading…</p>;
  const title = state.status === "ok" ? state.data.title_it : id;
  return <SessionView key={id} drillItemId={id} title={title} />;
}
