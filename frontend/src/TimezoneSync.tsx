import { useEffect, useRef } from "react";
import { updateSettings } from "./api/client";
import { useLearner } from "./learnerContext";
import { browserTimeZone } from "./timezone";

/**
 * Study days are local calendar days, so the backend needs the learner's zone. On first
 * load, if none was chosen yet ("UTC" is the backend default) and the browser's zone
 * differs, save it once. Failures are ignored: Settings lets the user set it by hand.
 */
export default function TimezoneSync() {
  const { learner, setLearner } = useLearner();
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    const current = learner.settings.timezone;
    const zone = browserTimeZone();
    if (current && current !== "UTC") return;
    if (!zone || zone === (current || "UTC")) return;
    updateSettings({ timezone: zone }).then(
      (settings) => setLearner({ ...learner, settings }),
      () => {},
    );
  }, [learner, setLearner]);
  return null;
}
