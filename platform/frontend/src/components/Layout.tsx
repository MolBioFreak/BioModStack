/** Shared shell for the local MolBio / NGS installation. */
import { NavLink } from 'react-router-dom';
import { ThemeSelector } from './ThemeSelector';
import { ProjectReturnBanner } from './project-manager/ProjectReturnBanner';

export function Layout({ children }: { children: React.ReactNode }) {
    return (
        <div className="h-screen flex flex-col overflow-x-hidden bg-surface text-content">
            <header className="flex shrink-0 flex-wrap items-center gap-4 border-b border-border-primary bg-surface-secondary px-4 py-3">
                <span className="font-semibold">BNS Plasmid Development Toolkit</span>
                <nav aria-label="Workspace navigation" className="flex flex-wrap items-center gap-2">
                    {[
                        ['/projects?scope=ngs-molbio', 'Projects'],
                        ['/designer', 'MolBio Viewer'],
                        ['/ngs', 'NGS'],
                    ].map(([to, label]) => (
                        <NavLink key={to} to={to} className={({ isActive }) =>
                            `rounded-lg px-3 py-2 text-sm font-semibold ${isActive ? 'bg-accent text-white' : 'text-content-secondary hover:bg-surface'}`
                        }>{label}</NavLink>
                    ))}
                </nav>
                <div className="ml-auto"><ThemeSelector /></div>
            </header>
            <main className="bms-layout-main min-h-0 flex-1 overflow-auto">
                <ProjectReturnBanner />
                {children}
            </main>
        </div>
    );
}

export default Layout;
