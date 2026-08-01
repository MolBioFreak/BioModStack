import type { ShapeGeometrySummary, ShapeLaunchRequest } from './api';

type ShapeLaunchSettings = Omit<ShapeLaunchRequest,
    'geometry_id' | 'expected_geometry_sha256' | 'expected_geometry_manifest_sha256' | 'expected_point_pool_sha256'>;

export const buildShapeLaunchRequest = (
    geometry: ShapeGeometrySummary,
    settings: ShapeLaunchSettings,
): ShapeLaunchRequest => ({
    ...settings,
    geometry_id: geometry.geometry_id,
    expected_geometry_sha256: geometry.geometry_sha256,
    expected_geometry_manifest_sha256: geometry.manifest_sha256,
    expected_point_pool_sha256: geometry.point_pool_sha256,
});
