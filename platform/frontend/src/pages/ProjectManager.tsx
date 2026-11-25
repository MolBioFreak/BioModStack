import { useParams, useSearchParams } from 'react-router-dom';
import { ProjectHubPage } from '../components/project-manager/ProjectHubPage';
import { ProjectIndexTable } from '../components/project-manager/ProjectIndexTable';
import { RetainedNativeResult } from '../components/project-manager/RetainedNativeResult';

export function ProjectManager() {
    const { projectId } = useParams<{ projectId?: string }>();
    const [searchParams] = useSearchParams();
    const retainedReceipt = searchParams.get('retained_receipt');

    if (projectId && retainedReceipt) {
        return (
            <RetainedNativeResult
                key={`${projectId}:${retainedReceipt}`}
                projectId={projectId}
                receiptId={retainedReceipt}
                returnUri={searchParams.get('return_uri')}
            />
        );
    }

    if (projectId) {
        return <ProjectHubPage projectId={projectId} />;
    }

    return <ProjectIndexTable />;
}

export default ProjectManager;
