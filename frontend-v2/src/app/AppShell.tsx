import { UserCircle2, Wrench } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { Toaster } from 'sonner';
import { Button } from '../components/ui/button';
import { HoverExplain } from '../components/ui/hover-explain';
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
  useSidebar
} from '../components/ui/sidebar';
import { TypographyH1, TypographyMuted, TypographySmall } from '../components/ui/typography';
import { logoutAuth } from '../lib/api/auth';
import { clearAuthToken } from '../lib/api/auth-token';
import { appNavGroups, type AppNavItem } from './navigation';
import { AppRoutes } from './AppRoutes';

const titleMap: Record<string, { title: string; subtitle: string }> = {
  '/dashboard': { title: '学习数据中心', subtitle: '每日进度、范围趋势与目标达成' },
  '/listening': { title: '听力上传与自动字幕', subtitle: '本地与 URL 上传，任务状态可观察' },
  '/listening/practice': { title: '听力练习', subtitle: '句级听写、快捷键与沉浸式输入' },
  '/reading': { title: '阅读强化工坊', subtitle: '分级改写、关键词讲解、理解题与复盘' },
  '/wallet': { title: '额度中心', subtitle: '兑换码充值、额度余额与档位说明' },
  '/profile': { title: '个人中心', subtitle: '管理英语等级与学习账户信息' }
};

const SIDEBAR_COLLAPSE_STORAGE_KEY = 'appSidebarCollapsed';

function isPathActive(path: string, currentPath: string) {
  const safePath = String(path || '').trim();
  const safeCurrent = String(currentPath || '').trim();
  if (!safePath) return false;
  return safeCurrent === safePath || safeCurrent.startsWith(`${safePath}/`);
}

function AppShellContent() {
  const navigate = useNavigate();
  const location = useLocation();
  const { setOpenMobile } = useSidebar();

  const closeMobile = useCallback(() => {
    setOpenMobile(false);
  }, [setOpenMobile]);

  const isLoginRoute = location.pathname === '/login';
  const header = useMemo(() => titleMap[location.pathname] || titleMap['/listening'], [location.pathname]);

  const isItemActive = useCallback((item: AppNavItem) => isPathActive(item.path, location.pathname), [location.pathname]);

  useEffect(() => {
    closeMobile();
  }, [closeMobile, location.pathname]);

  const handleLogout = useCallback(async () => {
    try {
      await logoutAuth();
    } catch {
      clearAuthToken();
    } finally {
      navigate('/login', { replace: true });
    }
  }, [navigate]);

  if (isLoginRoute) {
    return (
      <>
        <AppRoutes />
        <Toaster position="top-center" richColors closeButton />
      </>
    );
  }

  return (
    <>
      <Sidebar className="app-shell-sidebar-root" side="left" variant="sidebar" collapsible="icon">
        <SidebarHeader className="app-shell-sidebar__header">
          <div className="app-shell-sidebar__user">
            <HoverExplain asChild content="个人信息">
              <button type="button" className="app-shell-sidebar__user-avatar" onClick={() => {
                navigate('/profile');
                closeMobile();
              }}>
                <UserCircle2 size={18} strokeWidth={1.8} />
              </button>
            </HoverExplain>
            <div className="app-shell-sidebar__user-meta">
              <strong>shadcn learner</strong>
              <TypographySmall className="app-shell-sidebar__user-email">m@example.com</TypographySmall>
            </div>
          </div>
          <SidebarMenu className="app-shell-sidebar__profile-shortcut">
            <SidebarMenuItem>
              <SidebarMenuButton asChild isActive={isPathActive('/profile', location.pathname)} tooltip="个人中心 · 英语等级与账户说明">
                <NavLink to="/profile" title="个人中心 · 英语等级与账户说明" onClick={closeMobile}>
                  <Wrench size={18} strokeWidth={1.8} />
                  <span>个人中心</span>
                </NavLink>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>

        <SidebarContent className="app-shell-sidebar__content">
          {appNavGroups.map((group) => (
            <SidebarGroup key={group.id} className="app-shell-sidebar__group">
              <SidebarGroupLabel asChild>
                <TypographySmall>{group.label}</TypographySmall>
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    const active = isItemActive(item);
                    return (
                      <SidebarMenuItem key={item.id} className="app-shell-sidebar__menu-item">
                        <SidebarMenuButton asChild isActive={active} tooltip={`${item.label} · ${item.subtitle}`}>
                          <NavLink
                            to={item.path}
                            title={`${item.label} · ${item.subtitle}`}
                            onClick={closeMobile}
                          >
                            <Icon size={18} strokeWidth={1.8} />
                            <span>{item.label}</span>
                          </NavLink>
                        </SidebarMenuButton>
                      </SidebarMenuItem>
                    );
                  })}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          ))}
        </SidebarContent>

        <SidebarRail />
      </Sidebar>

      <SidebarInset className="app-main">
        <header className="app-header app-header--inset">
          <div className="app-header__left">
            <SidebarTrigger className="app-header__menu-btn" aria-label="切换侧边栏" />
            <div className="app-header__title-wrap">
              <TypographyH1>{header.title}</TypographyH1>
              <TypographyMuted>{header.subtitle}</TypographyMuted>
            </div>
          </div>

          <div className="app-header__right">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => navigate('/listening')}
            >
              新建听力任务
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => void handleLogout()}
            >
              退出登录
            </Button>
          </div>
        </header>

        <section className="app-main__content">
          <AppRoutes />
        </section>
      </SidebarInset>
      <Toaster position="top-center" richColors closeButton />
    </>
  );
}

export function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(() => {
    if (typeof window === 'undefined') return true;
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSE_STORAGE_KEY) !== '1';
    } catch {
      return true;
    }
  });

  return (
    <SidebarProvider
      className="app-layout"
      open={sidebarOpen}
      onOpenChange={setSidebarOpen}
      storageKey={SIDEBAR_COLLAPSE_STORAGE_KEY}
    >
      <AppShellContent />
    </SidebarProvider>
  );
}
