import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Obraz produkcyjny kopiuje tylko to, co potrzebne do uruchomienia - bez
  // node_modules i zrodel. Na serwerze z 3,8 GB RAM ma to znaczenie nie tylko
  // dla rozmiaru obrazu, ale i dla tego, ile zostaje na sam proces.
  output: "standalone",
  // Proxy /api/* na backend PO STRONIE SERWERA Next.js, zamiast wywolywac go
  // wprost z przegladarki pod innym hostem (NEXT_PUBLIC_API_URL). Bez tego
  // strona i API sa dla przegladarki dwoma roznymi "site" (np. amazon.lan vs
  // 192.168.20.20:8010), wiec ciasteczko sesji (SameSite=Lax) nigdy nie
  // wraca przy fetchach z frontu - logowanie zwraca 200, ale kazde kolejne
  // zapytanie wyglada jak niezalogowane. Z tym rewrite'em przegladarka widzi
  // WYLACZNIE origin strony (obojetnie ktory - amazon.lan przez nginx, albo
  // bezposrednio 192.168.20.20:3010), wiec ciasteczko jest zawsze same-site.
  // Adres backendu widziany PRZEZ SERWER Next (nie przez przegladarke).
  // Na VM to lokalny port uslugi systemd; w Dockerze - nazwa kontenera api.
  // Rewrites sa utrwalane przy budowaniu, wiec zmienna musi byc ustawiona
  // w chwili `npm run build`, nie dopiero przy starcie.
  async rewrites() {
    const api = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8010";
    return [
      { source: "/api/:path*", destination: `${api}/api/:path*` },
    ];
  },
};

export default nextConfig;
