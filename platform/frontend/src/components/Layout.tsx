/**
 * Layout - Persistent navigation wrapper for all pages
 */

import { Link, useLocation } from 'react-router-dom';
import { useState, useEffect } from 'react';

interface LayoutProps {
    children: React.ReactNode;
}

export function Layout({ children }: LayoutProps) {
    const location = useLocation();

    const isActive = (path: string) => location.pathname === path;

    return (
        <div className="min-h-screen bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900">
            {/* Top Navigation Bar */}
            <nav className="bg-slate-900/80 backdrop-blur-sm border-b border-slate-700/50 sticky top-0 z-50">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex items-center justify-between h-16">
                        {/* Logo / Brand */}
                        <Link to="/" className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-purple-500 to-pink-500 flex items-center justify-center">
                                <span className="text-white font-bold text-xl">B</span>
                            </div>
                            <span className="text-xl font-bold bg-gradient-to-r from-purple-400 to-pink-400 bg-clip-text text-transparent">
                                BioModStack
                            </span>
                        </Link>

                        {/* Navigation Links */}
                        <div className="flex items-center gap-2">
                            <Link
                                to="/"
                                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${isActive('/')
                                    ? 'bg-purple-500/20 text-purple-300'
                                    : 'text-slate-400 hover:text-white hover:bg-slate-700/50'
                                    }`}
                            >
                                Dashboard
                            </Link>
                            <Link
                                to="/submit"
                                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${isActive('/submit')
                                    ? 'bg-purple-500/20 text-purple-300'
                                    : 'text-slate-400 hover:text-white hover:bg-slate-700/50'
                                    }`}
                            >
                                New Job
                            </Link>
                            <Link
                                to="/designer"
                                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${isActive('/designer')
                                    ? 'bg-emerald-500/20 text-emerald-300'
                                    : 'text-slate-400 hover:text-white hover:bg-slate-700/50'
                                    }`}
                            >
                                BioDesigner
                            </Link>
                            <Link
                                to="/designs"
                                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${isActive('/designs')
                                    ? 'bg-purple-500/20 text-purple-300'
                                    : 'text-slate-400 hover:text-white hover:bg-slate-700/50'
                                    }`}
                            >
                                Results
                            </Link>

                            {/* Eco Mode Toggle */}
                            <EcoModeToggle />
                        </div>
                    </div>
                </div>
            </nav>

            {/* Main Content */}
            <main>
                {children}
            </main>
        </div>
    );
}

function EcoModeToggle() {
    const [powerPercent, setPowerPercent] = useState(100);
    const [enabled, setEnabled] = useState(false);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        const fetchState = () => {
            fetch('/api/gpu/power-control')
                .then(res => res.json())
                .then(data => {
                    setPowerPercent(data.power_percentage ?? 100);
                    setEnabled(data.enabled ?? false);
                })
                .catch(console.error);
        };

        fetchState();
        const interval = setInterval(fetchState, 5000);
        return () => clearInterval(interval);
    }, []);

    const togglePower = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/gpu/power-control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ toggle: true })
            });
            if (res.ok) {
                const data = await res.json();
                setPowerPercent(data.power_percentage ?? 100);
                setEnabled(data.enabled ?? false);
            }
        } catch (error) {
            console.error('Failed to toggle power mode:', error);
        } finally {
            setLoading(false);
        }
    };

    // Color based on state: gray when disabled, colored when enabled
    const getColorClasses = () => {
        if (!enabled) return 'bg-slate-800 text-slate-400 border-slate-700 hover:border-slate-600';
        if (powerPercent < 50) return 'bg-green-500/20 text-green-400 border-green-500/30 hover:bg-green-500/30';
        if (powerPercent < 80) return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30 hover:bg-yellow-500/30';
        return 'bg-purple-500/20 text-purple-400 border-purple-500/30 hover:bg-purple-500/30';
    };

    const getDotColor = () => {
        if (!enabled) return 'bg-slate-600';
        if (powerPercent < 50) return 'bg-green-400';
        if (powerPercent < 80) return 'bg-yellow-400';
        return 'bg-purple-400';
    };

    return (
        <button
            onClick={togglePower}
            disabled={loading}
            className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition-all border ${getColorClasses()}`}
        >
            {loading ? (
                <div className="w-3 h-3 border-2 border-current border-t-transparent rounded-full animate-spin" />
            ) : (
                <div className={`w-2 h-2 rounded-full ${getDotColor()}`} />
            )}
            {enabled ? `${powerPercent}% PWR` : 'OFF'}
        </button>
    );
}

export default Layout;
