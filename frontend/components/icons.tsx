import type { ReactNode, SVGProps } from "react";

function Svg({ children, ...p }: SVGProps<SVGSVGElement> & { children: ReactNode }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden {...p}>
      {children}
    </svg>
  );
}
export const PlusIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M12 5v14M5 12h14" /></Svg>;
export const SendIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M12 19V5M5 12l7-7 7 7" /></Svg>;
export const SearchIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><circle cx="11" cy="11" r="6.5" /><path d="m20 20-3.5-3.5" /></Svg>;
export const FileIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></Svg>;
export const GlobeIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18" /></Svg>;
export const TrashIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3" /></Svg>;
export const MenuIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M4 7h16M4 12h16M4 17h16" /></Svg>;
export const CloseIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M6 6l12 12M18 6 6 18" /></Svg>;
export const CopyIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></Svg>;
export const CheckIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="m5 12.5 4.5 4.5L19 7.5" /></Svg>;
export const LogoutIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 17l-5-5 5-5M5 12h11" /></Svg>;
export const ChatIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="M5 5h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-7l-5 4v-4H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" /></Svg>;
export const StackIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5" /></Svg>;
export const MicIcon = (p: SVGProps<SVGSVGElement>) => <Svg {...p}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></Svg>;
