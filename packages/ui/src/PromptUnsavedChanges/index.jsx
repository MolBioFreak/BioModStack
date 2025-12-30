/* Copyright (C) 2018 TeselaGen Biotechnology, Inc. */

import React, { useEffect } from "react";
// import { Prompt } from "react-router-dom";

export const defaultMessagge =
  "Are you sure you want to leave? There are unsaved changes.";

const warnBeforeLeave = e => {
  // if we are in a cypress test run then we don't want to block navigation
  if (window.Cypress) {
    return null;
  }

  // Maintains older browser compatibility. In newer versions a generic string is returned
  (e || window.event).returnValue = defaultMessagge; //Gecko + IE
  return defaultMessagge; //Webkit, Safari, Chrome
};

export function PromptUnsavedChanges({
  message = defaultMessagge,
  when = false
}) {
  useEffect(() => {
    if (when) {
      window.addEventListener("beforeunload", warnBeforeLeave);
    }
    return () => window.removeEventListener("beforeunload", warnBeforeLeave);
  });

  if (window.Cypress) {
    return null;
  }

  // TODO: Re-implement SPA navigation blocking using useBlocker from react-router-dom v6/v7
  // <Prompt> is no longer supported.
  return null;
}

export default PromptUnsavedChanges;
