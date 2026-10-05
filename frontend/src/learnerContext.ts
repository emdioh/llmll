import { createContext, useContext } from "react";
import type { Learner } from "./api/client";

export interface LearnerContextValue {
  learner: Learner;
  setLearner: (learner: Learner) => void;
}

export const LearnerContext = createContext<LearnerContextValue | null>(null);

export function useLearner(): LearnerContextValue {
  const value = useContext(LearnerContext);
  if (!value) throw new Error("useLearner must be used inside LearnerGate");
  return value;
}
