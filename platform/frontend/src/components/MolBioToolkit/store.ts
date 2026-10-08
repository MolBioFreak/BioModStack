
import { createStore, combineReducers, applyMiddleware, compose } from 'redux';
import type { Middleware } from 'redux';
import thunk from 'redux-thunk';
// @ts-ignore - OVE package is untyped
import { vectorEditorReducer as vectorEditorReducerFactory, vectorEditorMiddleware } from '@biomodstack/ove';

// Extend window interface for Redux DevTools
declare global {
    interface Window {
        __REDUX_DEVTOOLS_EXTENSION_COMPOSE__?: typeof compose;
    }
}

// The editor name must match what's used in the <Editor editorName="..." /> prop
const EDITOR_NAME = 'MolBioToolkitEditor';

// vectorEditorReducer is actually a FACTORY function that returns the real reducer.
// It must be called with an initial state object containing editor names as keys.
// @ts-ignore - Factory function takes initial state, not action
const vectorEditorReducer = vectorEditorReducerFactory({
    [EDITOR_NAME]: {}  // Initialize with an empty state for our editor
});

// 1. Combine Reducers
// The OVE editor expects its state to be mounted at 'VectorEditor'
const rootReducer = combineReducers({
    VectorEditor: vectorEditorReducer,
});

// 2. Configure Middleware
// OVE requires redux-thunk and its own vectorEditorMiddleware
const middleware: Middleware[] = [thunk, vectorEditorMiddleware];

// 3. Setup Enhancers (DevTools)
const composeEnhancers =
    (typeof window !== 'undefined' && window.__REDUX_DEVTOOLS_EXTENSION_COMPOSE__) || compose;

// 4. Create Store
export const store = createStore(
    rootReducer,
    composeEnhancers(applyMiddleware(...middleware))
);

export type RootState = ReturnType<typeof rootReducer>;
export type AppDispatch = typeof store.dispatch;

