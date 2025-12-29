// Type declarations for @biomodstack/ove
// This package is written in JSX without TypeScript, so we declare its exports here.

declare module '@biomodstack/ove' {
  import { ComponentType } from 'react';
  import { Middleware, Reducer } from 'redux';

  // Editor component
  export interface EditorProps {
    sequenceData?: any;
    onSave?: (data: any) => void;
    editorName: string;
    [key: string]: any;
  }
  export const Editor: ComponentType<EditorProps>;
  export const EditorUnconnected: ComponentType<EditorProps>;

  // Redux
  export const vectorEditorReducer: Reducer;
  export const vectorEditorMiddleware: Middleware;
  export const actions: Record<string, any>;

  // HOCs
  export function withEditorProps(Component: ComponentType<any>): ComponentType<any>;
  export function connectToEditor(mapStateToProps?: (state: any) => any): (Component: ComponentType<any>) => ComponentType<any>;
  export function withEditorInteractions(Component: ComponentType<any>): ComponentType<any>;

  // Utils
  export function updateEditor(store: any, editorName: string, options?: any): void;
  export function addAlignment(store: any, options?: any): void;

  // Views
  export const CircularView: ComponentType<any>;
  export const CircularViewUnconnected: ComponentType<any>;
  export const LinearView: ComponentType<any>;
  export const LinearViewUnconnected: ComponentType<any>;
  export const RowView: ComponentType<any>;
  export const RowViewUnconnected: ComponentType<any>;
  export const RowItem: ComponentType<any>;
  export const StatusBar: ComponentType<any>;
  export const StatusBarUnconnected: ComponentType<any>;
  export const ToolBar: ComponentType<any>;
  export const CutsiteFilter: ComponentType<any>;
  export const CutsiteFilterUnconnected: ComponentType<any>;
  export const DigestTool: ComponentType<any>;
  export const DigestToolUnconnected: ComponentType<any>;
  export const EnzymeViewer: ComponentType<any>;
  export const AlignmentView: ComponentType<any>;
  export const SimpleCircularOrLinearView: ComponentType<any>;
}
