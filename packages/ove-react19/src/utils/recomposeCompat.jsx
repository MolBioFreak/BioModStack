/**
 * React 19 compatible replacements for recompose HOCs
 * 
 * These utilities provide drop-in replacements for recompose functions
 * that are incompatible with React 19.
 */

import React, { useMemo, useCallback, useEffect, useRef, memo } from 'react';

/**
 * Replacement for recompose's withHandlers HOC
 * 
 * Takes a handlers config object where each key is a handler name
 * and the value is a function that takes props and returns a handler function.
 * 
 * @param {Object} handlerCreators - Object of handler creator functions
 * @returns {Function} - HOC that injects handlers as props
 * 
 * @example
 * // Before (recompose):
 * withHandlers({
 *   handleSave: props => () => props.onSave(props.data),
 *   handleClick: props => (e) => props.onClick(e, props.id)
 * })
 * 
 * // After (this utility):
 * withHandlersHook({
 *   handleSave: props => () => props.onSave(props.data),
 *   handleClick: props => (e) => props.onClick(e, props.id)
 * })
 */
export function withHandlersHook(handlerCreators) {
    return function (WrappedComponent) {
        const WithHandlers = React.forwardRef(({ key: _key, ...props }, ref) => {
            // Create handlers by calling each handler creator with current props
            // We use useMemo to avoid recreating handlers on every render
            // Note: This is intentionally NOT using useCallback for each handler
            // because the original recompose withHandlers recreates handlers when props change
            const handlers = useMemo(() => {
                const result = {};
                for (const [handlerKey, creator] of Object.entries(handlerCreators)) {
                    if (typeof creator === 'function') {
                        result[handlerKey] = creator(props);
                    }
                }
                return result;
            }, [props]);

            return <WrappedComponent {...props} {...handlers} ref={ref} />;
        });

        WithHandlers.displayName = `withHandlersHook(${getDisplayName(WrappedComponent)})`;
        return WithHandlers;
    };
}

/**
 * Replacement for recompose's withProps HOC
 * 
 * @param {Object|Function} propsOrMapper - Object of props or function that returns props
 * @returns {Function} - HOC that injects additional props
 */
export function withPropsHook(propsOrMapper) {
    return function (WrappedComponent) {
        const WithProps = React.forwardRef(({ key: _key, ...props }, ref) => {
            const additionalProps = useMemo(() => {
                if (typeof propsOrMapper === 'function') {
                    return propsOrMapper(props);
                }
                return propsOrMapper;
            }, [props]);

            return <WrappedComponent {...props} {...additionalProps} ref={ref} />;
        });

        WithProps.displayName = `withPropsHook(${getDisplayName(WrappedComponent)})`;
        return WithProps;
    };
}

/**
 * Replacement for recompose's lifecycle HOC
 * 
 * @param {Object} spec - Lifecycle specification object with componentDidMount, etc.
 * @returns {Function} - HOC that adds lifecycle behavior
 */
export function lifecycleHook(spec) {
    return function (WrappedComponent) {
        const WithLifecycle = React.forwardRef(({ key: _key, ...props }, ref) => {
            const propsRef = useRef(props);
            propsRef.current = props;

            useEffect(() => {
                const context = {
                    props: propsRef.current,
                    setState: () => {
                        console.warn('setState in lifecycle hook is not supported in React 19 migration');
                    }
                };

                // componentDidMount
                if (spec.componentDidMount) {
                    spec.componentDidMount.call(context);
                }

                // componentWillUnmount
                return () => {
                    if (spec.componentWillUnmount) {
                        spec.componentWillUnmount.call({ ...context, props: propsRef.current });
                    }
                };
            }, []);

            // componentDidUpdate - runs on every prop change except first render
            const isFirstRender = useRef(true);
            const prevPropsRef = useRef(props);

            useEffect(() => {
                if (isFirstRender.current) {
                    isFirstRender.current = false;
                    prevPropsRef.current = props;
                    return;
                }

                if (spec.componentDidUpdate) {
                    const context = {
                        props,
                        setState: () => {
                            console.warn('setState in lifecycle hook is not supported in React 19 migration');
                        }
                    };
                    spec.componentDidUpdate.call(context, prevPropsRef.current);
                }
                prevPropsRef.current = props;
            }, [props]);

            return <WrappedComponent {...props} ref={ref} />;
        });

        WithLifecycle.displayName = `lifecycleHook(${getDisplayName(WrappedComponent)})`;
        return WithLifecycle;
    };
}

/**
 * Replacement for recompose's branch HOC
 * 
 * @param {Function} test - Function that receives props and returns boolean
 * @param {Function} left - HOC to apply if test is true
 * @param {Function} [right] - Optional HOC to apply if test is false (default: identity)
 * @returns {Function} - HOC that conditionally applies transformations
 */
export function branchHook(test, left, right = x => x) {
    return function (WrappedComponent) {
        const LeftComponent = left(WrappedComponent);
        const RightComponent = right(WrappedComponent);

        const Branch = ({ key: _key, ...props }) => {
            if (test(props)) {
                return <LeftComponent {...props} />;
            }
            return <RightComponent {...props} />;
        };

        Branch.displayName = `branchHook(${getDisplayName(WrappedComponent)})`;
        return Branch;
    };
}

/**
 * Replacement for recompose's renderComponent
 * Used with branch to render an alternative component
 * 
 * @param {React.Component} Component - Component to render
 * @returns {Function} - HOC that renders the specified component
 */
export function renderComponentHook(Component) {
    return function () {
        const RenderComponent = ({ key: _key, ...props }) => <Component {...props} />;
        RenderComponent.displayName = `renderComponentHook(${getDisplayName(Component)})`;
        return RenderComponent;
    };
}

/**
 * Replacement for recompose's shouldUpdate / onlyUpdateForKeys
 * 
 * @param {Function} shouldUpdateFn - Function that receives (props, nextProps) and returns boolean
 * @returns {Function} - HOC that controls updates
 */
export function shouldUpdateHook(shouldUpdateFn) {
    return function (WrappedComponent) {
        return memo(WrappedComponent, (prevProps, nextProps) => {
            // memo uses the opposite logic: return true to SKIP update
            return !shouldUpdateFn(nextProps, prevProps);
        });
    };
}

/**
 * Replacement for recompose's onlyUpdateForKeys
 * 
 * @param {string[]} keys - Array of prop keys to check for changes
 * @returns {Function} - HOC that only updates when specified keys change
 */
export function onlyUpdateForKeysHook(keys) {
    return shouldUpdateHook((nextProps, prevProps) => {
        return keys.some(key => prevProps[key] !== nextProps[key]);
    });
}

/**
 * Replacement for recompose's mapProps
 * 
 * @param {Function} propsMapper - Function that maps props to new props
 * @returns {Function} - HOC that transforms props
 */
export function mapPropsHook(propsMapper) {
    return function (WrappedComponent) {
        const MapProps = React.forwardRef(({ key: _key, ...props }, ref) => {
            const mappedProps = useMemo(() => propsMapper(props), [props]);
            return <WrappedComponent {...mappedProps} ref={ref} />;
        });

        MapProps.displayName = `mapPropsHook(${getDisplayName(WrappedComponent)})`;
        return MapProps;
    };
}

/**
 * Replacement for recompose's compose
 * Simply re-exports Redux's compose for compatibility
 */
export { compose } from 'redux';

// Helper to get component display name
function getDisplayName(Component) {
    return Component.displayName || Component.name || 'Component';
}
