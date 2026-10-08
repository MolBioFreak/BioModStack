import { createContext, useContext } from 'react';

/**
 * Context for Blueprint.js portal class name configuration.
 * Replaces the legacy getChildContext/childContextTypes pattern
 * that was removed in React 19.
 *
 * This provides the CSS class name that Blueprint.js uses
 * for portal containers (dialogs, tooltips, popovers, etc.)
 */
export const BlueprintPortalContext = createContext("ove-portal");

/**
 * Hook to access the Blueprint portal class name
 * @returns {string} The portal class name (default: "ove-portal")
 */
export function useBlueprintPortalClassName() {
    return useContext(BlueprintPortalContext);
}

export default BlueprintPortalContext;
