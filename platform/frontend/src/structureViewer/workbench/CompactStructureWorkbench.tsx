import StructureViewerHost, { type StructureViewerHostProps } from '../StructureViewerHost';

export function CompactStructureWorkbench(props: StructureViewerHostProps) {
    return <StructureViewerHost {...props} showMetricWorkbench={false} hideControls={props.hideControls ?? true} />;
}
