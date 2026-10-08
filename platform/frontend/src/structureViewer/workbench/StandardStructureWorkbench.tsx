import StructureViewerHost, { type StructureViewerHostProps } from '../StructureViewerHost';

export function StandardStructureWorkbench(props: StructureViewerHostProps) {
    return <StructureViewerHost {...props} showMetricWorkbench={props.showMetricWorkbench ?? true} hideControls={props.hideControls ?? false} />;
}
