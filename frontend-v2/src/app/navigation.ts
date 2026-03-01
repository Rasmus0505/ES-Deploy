import { AudioLines, BookOpenText, LayoutDashboard, UploadCloud, WalletCards } from 'lucide-react';

export type AppNavItem = {
  id: string;
  path: string;
  label: string;
  subtitle: string;
  icon: typeof LayoutDashboard;
};

export type AppNavGroup = {
  id: string;
  label: string;
  items: AppNavItem[];
};

export const appNavGroups: AppNavGroup[] = [
  {
    id: 'platform',
    label: '学习平台',
    items: [
      {
        id: 'dashboard',
        path: '/dashboard',
        label: '数据中心',
        subtitle: '趋势与目标',
        icon: LayoutDashboard
      },
      {
        id: 'listening',
        path: '/listening',
        label: '听力',
        subtitle: '上传与自动字幕',
        icon: AudioLines
      },
      {
        id: 'reading',
        path: '/reading',
        label: '阅读强化',
        subtitle: '分级改写与理解题',
        icon: BookOpenText
      },
      {
        id: 'wallet',
        path: '/wallet',
        label: '额度中心',
        subtitle: '兑换码充值与余额',
        icon: WalletCards
      }
    ]
  },
  {
    id: 'tools',
    label: '工具',
    items: [
      {
        id: 'upload-task',
        path: '/listening',
        label: '新建任务',
        subtitle: '上传视频并自动生成字幕',
        icon: UploadCloud
      }
    ]
  }
];
