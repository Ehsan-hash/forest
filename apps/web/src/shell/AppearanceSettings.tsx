import { Field, useUI } from "../ui";

export function AppearanceSettings({ theme, setTheme, reducedTransparency, setReducedTransparency }: { theme: string; setTheme: (value: string) => void; reducedTransparency: boolean; setReducedTransparency: (value: boolean) => void }) {
  const { t } = useUI();
  return <section className="page appearance-settings" aria-label={t("外观", "Appearance")}><div className="surface">
    <h2>{t("外观", "Appearance")}</h2>
    <Field label={t("主题", "Theme")}><select value={theme} onChange={(event) => setTheme(event.target.value)}><option value="light">{t("浅色", "Light")}</option><option value="dark">{t("深色", "Dark")}</option><option value="system">{t("跟随系统", "System")}</option></select></Field>
    <label className="checkbox-field"><input type="checkbox" checked={reducedTransparency} onChange={(event) => setReducedTransparency(event.target.checked)} />{t("减少透明效果", "Reduce transparency")}</label>
  </div></section>;
}
