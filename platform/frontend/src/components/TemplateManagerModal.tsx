/**
 * TemplateManagerModal - Modal for saving current job configuration as a reusable template
 * 
 * Features:
 * - Save current job params as a named template
 * - Edit existing user templates
 * - Delete user templates
 * - Visual icon/color customization
 */

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    fetchUserTemplates,
    createUserTemplate,
    updateUserTemplate,
    deleteUserTemplate,
} from '../lib/api';
import type { UserTemplate, UserTemplateCreate } from '../lib/api';

interface TemplateManagerModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSelect?: (template: UserTemplate) => void;
    // For saving current config as template
    currentParams?: Record<string, any>;
    currentModelId?: string;
    currentMode?: string;
    baseTemplateId?: string;
}

// Available icons for templates
const TEMPLATE_ICONS = ['bookmark', 'star', 'heart', 'bolt', 'beaker', 'cog', 'cube', 'fire'];
const TEMPLATE_COLORS = [
    '#6B7280', '#EF4444', '#F59E0B', '#10B981', '#3B82F6', '#8B5CF6', '#EC4899', '#06B6D4'
];

export function TemplateManagerModal({
    isOpen,
    onClose,
    onSelect,
    currentParams,
    currentModelId,
    currentMode,
    baseTemplateId
}: TemplateManagerModalProps) {
    const queryClient = useQueryClient();

    // View mode
    const [mode, setMode] = useState<'list' | 'edit'>('list');
    const [editingTemplate, setEditingTemplate] = useState<UserTemplate | null>(null);
    const [searchQuery, setSearchQuery] = useState('');

    // Form state
    const [formName, setFormName] = useState('');
    const [formDescription, setFormDescription] = useState('');
    const [formIcon, setFormIcon] = useState('bookmark');
    const [formColor, setFormColor] = useState('#6B7280');
    const [formError, setFormError] = useState('');

    // Fetch templates
    const { data: templates = [], isLoading } = useQuery({
        queryKey: ['user-templates', searchQuery],
        queryFn: () => fetchUserTemplates(searchQuery || undefined),
        enabled: isOpen,
        select: (res) => res.data,
    });

    // Create mutation
    const createMutation = useMutation({
        mutationFn: createUserTemplate,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user-templates'] });
            resetForm();
            setMode('list');
        },
        onError: (err: any) => {
            setFormError(err.response?.data?.detail || 'Failed to create template');
        }
    });

    // Update mutation
    const updateMutation = useMutation({
        mutationFn: ({ id, data }: { id: string; data: Partial<UserTemplateCreate> }) =>
            updateUserTemplate(id, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user-templates'] });
            resetForm();
            setMode('list');
        },
        onError: (err: any) => {
            setFormError(err.response?.data?.detail || 'Failed to update template');
        }
    });

    // Delete mutation
    const deleteMutation = useMutation({
        mutationFn: deleteUserTemplate,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user-templates'] });
        }
    });

    // Reset form
    const resetForm = () => {
        setFormName('');
        setFormDescription('');
        setFormIcon('bookmark');
        setFormColor('#6B7280');
        setFormError('');
        setEditingTemplate(null);
    };

    // Initialize form when saving current config
    useEffect(() => {
        if (isOpen && currentParams && !editingTemplate && mode === 'list') {
            // Auto-switch to edit mode if we have params to save
            if (Object.keys(currentParams).length > 0) {
                setMode('edit');
            }
        }
    }, [isOpen, currentParams]);

    // Populate form when editing
    useEffect(() => {
        if (editingTemplate) {
            setFormName(editingTemplate.name);
            setFormDescription(editingTemplate.description || '');
            setFormIcon(editingTemplate.icon);
            setFormColor(editingTemplate.color);
        }
    }, [editingTemplate]);

    // Handle form submission
    const handleSubmit = () => {
        setFormError('');

        if (!formName.trim()) {
            setFormError('Name is required');
            return;
        }

        const data: UserTemplateCreate = {
            name: formName.trim(),
            description: formDescription.trim() || undefined,
            icon: formIcon,
            color: formColor,
            base_template_id: editingTemplate?.base_template_id || baseTemplateId,
            model_id: editingTemplate?.model_id || currentModelId,
            mode: editingTemplate?.mode || currentMode,
            params: editingTemplate?.params || currentParams || {},
        };

        if (editingTemplate) {
            updateMutation.mutate({ id: editingTemplate.id, data });
        } else {
            createMutation.mutate(data);
        }
    };

    // Handle delete
    const handleDelete = (template: UserTemplate) => {
        if (confirm(`Delete template "${template.name}"? This cannot be undone.`)) {
            deleteMutation.mutate(template.id);
        }
    };

    // Get icon display
    const getIconEmoji = (icon: string) => {
        const iconMap: Record<string, string> = {
            bookmark: '🔖',
            star: '⭐',
            heart: '❤️',
            bolt: '⚡',
            beaker: '🧪',
            cog: '⚙️',
            cube: '🧊',
            fire: '🔥',
        };
        return iconMap[icon] || '📋';
    };

    // Handle close
    const handleClose = () => {
        resetForm();
        setMode('list');
        onClose();
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl">
                {/* Header */}
                <div className="p-5 border-b border-slate-700 flex justify-between items-center bg-slate-800/50 rounded-t-xl">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-purple-500/20 flex items-center justify-center text-purple-400 text-xl">
                            📋
                        </div>
                        <div>
                            <h3 className="font-semibold text-slate-200 text-lg">
                                {mode === 'list' ? 'My Templates' : editingTemplate ? 'Edit Template' : 'Save as Template'}
                            </h3>
                            <p className="text-xs text-slate-500">
                                {mode === 'list' ? 'Manage your saved run configurations' : 'Configure template details'}
                            </p>
                        </div>
                    </div>
                    <button
                        onClick={handleClose}
                        className="p-2 hover:bg-slate-700 rounded-lg text-slate-400 hover:text-white transition-colors"
                    >
                        ✕
                    </button>
                </div>

                {/* Content */}
                <div className="flex-1 overflow-y-auto p-5">
                    {mode === 'list' ? (
                        /* List View */
                        <div className="space-y-4">
                            {/* Search and Add */}
                            <div className="flex gap-3">
                                <input
                                    type="text"
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    placeholder="Search templates..."
                                    className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                                />
                                {currentParams && Object.keys(currentParams).length > 0 && (
                                    <button
                                        onClick={() => { resetForm(); setMode('edit'); }}
                                        className="px-4 py-2.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                                    >
                                        <span>+</span> Save Current Config
                                    </button>
                                )}
                            </div>

                            {/* Template List */}
                            {isLoading ? (
                                <div className="flex items-center justify-center py-12">
                                    <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-purple-500" />
                                </div>
                            ) : templates.length === 0 ? (
                                <div className="text-center py-12 text-slate-500">
                                    <div className="text-4xl mb-3">📭</div>
                                    <p>No saved templates yet</p>
                                    <p className="text-sm mt-1">Configure a job and save it as a template</p>
                                </div>
                            ) : (
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    {templates.map((template) => (
                                        <div
                                            key={template.id}
                                            className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 hover:border-slate-600 transition-colors group"
                                        >
                                            <div className="flex items-start gap-3">
                                                <div
                                                    className="w-10 h-10 rounded-lg flex items-center justify-center text-lg shrink-0"
                                                    style={{ backgroundColor: `${template.color}20`, color: template.color }}
                                                >
                                                    {getIconEmoji(template.icon)}
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <h4 className="font-medium text-slate-200 truncate">{template.name}</h4>
                                                    {template.description && (
                                                        <p className="text-xs text-slate-500 line-clamp-1 mt-0.5">{template.description}</p>
                                                    )}
                                                    <div className="flex items-center gap-2 mt-2">
                                                        {template.model_id && (
                                                            <span className="text-xs bg-slate-700 px-2 py-0.5 rounded text-slate-400">
                                                                {template.model_id}
                                                            </span>
                                                        )}
                                                        <span className="text-xs text-slate-600">
                                                            {Object.keys(template.params).length} params
                                                        </span>
                                                    </div>
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-2 mt-3 pt-3 border-t border-slate-700/50">
                                                {onSelect && (
                                                    <button
                                                        onClick={() => { onSelect(template); handleClose(); }}
                                                        className="flex-1 px-3 py-1.5 bg-purple-600 hover:bg-purple-500 text-white text-sm rounded transition-colors"
                                                    >
                                                        Load
                                                    </button>
                                                )}
                                                <button
                                                    onClick={() => { setEditingTemplate(template); setMode('edit'); }}
                                                    className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm rounded transition-colors"
                                                >
                                                    Edit
                                                </button>
                                                <button
                                                    onClick={() => handleDelete(template)}
                                                    className="px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 text-red-400 text-sm rounded transition-colors"
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    ) : (
                        /* Edit/Create View */
                        <div className="space-y-5">
                            {/* Back button */}
                            <button
                                onClick={() => { resetForm(); setMode('list'); }}
                                className="text-slate-400 hover:text-white text-sm flex items-center gap-1 transition-colors"
                            >
                                ← Back to list
                            </button>

                            {/* Form */}
                            <div className="space-y-5">
                                {/* Name */}
                                <div>
                                    <label className="block text-sm font-medium text-slate-400 mb-1">
                                        Template Name <span className="text-red-400">*</span>
                                    </label>
                                    <input
                                        type="text"
                                        value={formName}
                                        onChange={(e) => setFormName(e.target.value)}
                                        placeholder="e.g., My Boltz Config"
                                        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                                    />
                                </div>

                                {/* Description */}
                                <div>
                                    <label className="block text-sm font-medium text-slate-400 mb-1">
                                        Description
                                    </label>
                                    <input
                                        type="text"
                                        value={formDescription}
                                        onChange={(e) => setFormDescription(e.target.value)}
                                        placeholder="Brief description of what this template is for..."
                                        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-purple-500 outline-none"
                                    />
                                </div>

                                {/* Icon and Color */}
                                <div className="grid grid-cols-2 gap-5">
                                    <div>
                                        <label className="block text-sm font-medium text-slate-400 mb-2">
                                            Icon
                                        </label>
                                        <div className="flex flex-wrap gap-2">
                                            {TEMPLATE_ICONS.map((icon) => (
                                                <button
                                                    key={icon}
                                                    onClick={() => setFormIcon(icon)}
                                                    className={`w-10 h-10 rounded-lg flex items-center justify-center text-lg transition-all ${formIcon === icon
                                                        ? 'bg-purple-500/30 border-2 border-purple-500'
                                                        : 'bg-slate-800 border border-slate-700 hover:border-slate-600'
                                                        }`}
                                                >
                                                    {getIconEmoji(icon)}
                                                </button>
                                            ))}
                                        </div>
                                    </div>
                                    <div>
                                        <label className="block text-sm font-medium text-slate-400 mb-2">
                                            Color
                                        </label>
                                        <div className="flex flex-wrap gap-2">
                                            {TEMPLATE_COLORS.map((color) => (
                                                <button
                                                    key={color}
                                                    onClick={() => setFormColor(color)}
                                                    className={`w-10 h-10 rounded-lg transition-all ${formColor === color
                                                        ? 'ring-2 ring-white ring-offset-2 ring-offset-slate-900'
                                                        : 'hover:scale-110'
                                                        }`}
                                                    style={{ backgroundColor: color }}
                                                />
                                            ))}
                                        </div>
                                    </div>
                                </div>

                                {/* Preview */}
                                <div className="bg-slate-800/50 border border-slate-700 rounded-lg p-4">
                                    <p className="text-xs text-slate-500 mb-2">Preview:</p>
                                    <div className="flex items-center gap-3">
                                        <div
                                            className="w-12 h-12 rounded-lg flex items-center justify-center text-xl"
                                            style={{ backgroundColor: `${formColor}20`, color: formColor }}
                                        >
                                            {getIconEmoji(formIcon)}
                                        </div>
                                        <div>
                                            <p className="font-medium text-slate-200">{formName || 'Template Name'}</p>
                                            <p className="text-xs text-slate-500">{formDescription || 'No description'}</p>
                                        </div>
                                    </div>
                                </div>

                                {/* Params info */}
                                {!editingTemplate && currentParams && (
                                    <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg px-4 py-3 text-blue-400 text-sm">
                                        <p className="font-medium mb-1">Parameters to save:</p>
                                        <p className="text-xs text-blue-300/70">
                                            {Object.keys(currentParams).length} parameters from current configuration
                                        </p>
                                    </div>
                                )}
                            </div>

                            {/* Error */}
                            {formError && (
                                <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm">
                                    {formError}
                                </div>
                            )}

                            {/* Actions */}
                            <div className="flex justify-end gap-3 pt-3 border-t border-slate-700">
                                <button
                                    onClick={() => { resetForm(); setMode('list'); }}
                                    className="px-5 py-2.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg font-medium transition-colors"
                                >
                                    Cancel
                                </button>
                                <button
                                    onClick={handleSubmit}
                                    disabled={createMutation.isPending || updateMutation.isPending}
                                    className="px-5 py-2.5 bg-purple-600 hover:bg-purple-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
                                >
                                    {createMutation.isPending || updateMutation.isPending
                                        ? 'Saving...'
                                        : editingTemplate
                                            ? 'Update Template'
                                            : 'Save Template'
                                    }
                                </button>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
