import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthGate } from "@/components/AuthGate";
import { Providers } from "@/components/Providers";
import { Shell } from "@/components/Shell";
import { FiltersProvider } from "@/lib/filters";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "AdsPanel",
  description: "Panel zarządzania kampaniami Amazon Ads",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pl" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <head>
        {/* Przywraca zapisany motyw przed pierwszym malowaniem - bez tego
            strona mignelaby jasnym tlem u kogos, kto wybral ciemny. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var t=localStorage.getItem('adspanel.theme');if(t)document.documentElement.dataset.theme=t}catch(e){}",
          }}
        />
      </head>
      <body className="min-h-full">
        <Providers>
          <AuthGate>
            <FiltersProvider>
              <Shell>{children}</Shell>
            </FiltersProvider>
          </AuthGate>
        </Providers>
      </body>
    </html>
  );
}
