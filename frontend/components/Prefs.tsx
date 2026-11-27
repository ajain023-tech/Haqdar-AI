"use client";
// App-wide preferences: UI language and large text, remembered on the device.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { STRINGS, type Strings, type UiLang } from "@/lib/i18n";

interface Prefs {
  lang: UiLang;
  setLang: (l: UiLang) => void;
  large: boolean;
  setLarge: (v: boolean) => void;
  t: Strings;
}

const Ctx = createContext<Prefs | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<UiLang>("hi");
  const [large, setLarge] = useState(false);

  useEffect(() => {
    const l = localStorage.getItem("haqdar.lang");
    if (l === "en" || l === "hi") setLang(l);
    else if (!navigator.language.startsWith("hi")) setLang("en");
    setLarge(localStorage.getItem("haqdar.large") === "1");
  }, []);

  useEffect(() => {
    localStorage.setItem("haqdar.lang", lang);
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    localStorage.setItem("haqdar.large", large ? "1" : "0");
    document.documentElement.classList.toggle("text-large", large);
  }, [large]);

  return <Ctx.Provider value={{ lang, setLang, large, setLarge, t: STRINGS[lang] as Strings }}>{children}</Ctx.Provider>;
}

export function usePrefs(): Prefs {
  const v = useContext(Ctx);
  if (!v) throw new Error("usePrefs outside PrefsProvider");
  return v;
}
