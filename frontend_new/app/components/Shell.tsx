"use client";

/** App shell with role-aware navigation. */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, tokens } from "../lib/api";

type Me = {
  type: "user" | "customer_user";
  name: string | null;
  role: string;
  customer_id: number | null;
  permissions: string[];
};

const DEALER_NAV = [
  { href: "/", label: "Home" },
  { href: "/order", label: "New order" },
  { href: "/orders", label: "My orders" },
  { href: "/ledger", label: "Ledger" },
  { href: "/pay", label: "Pay" },
];

const STAFF_NAV = [
  { href: "/admin", label: "Dashboard", perm: "report.read" },
  { href: "/admin/orders", label: "Orders", perm: "order.read" },
  { href: "/admin/credit", label: "Credit", perm: "credit.read" },
  { href: "/admin/cash", label: "Cash queue", perm: "payment.confirm_cash" },
  { href: "/admin/follow-ups", label: "Follow-ups", perm: "order.read" },
  { href: "/admin/customers", label: "Customers", perm: "customer.read" },
  { href: "/admin/settings", label: "Settings", perm: "settings.write" },
];

export function Shell({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    void (async () => {
      if (!tokens.access) {
        setLoading(false);
        return;
      }
      try {
        setMe(await api.get<Me>("/auth/me"));
      } catch {
        tokens.clear();
      } finally {
        setLoading(false);
      }
    })();
  }, [pathname]);

  const isAuthPage = pathname?.startsWith("/login");

  useEffect(() => {
    if (!loading && !me && !isAuthPage) router.replace("/login");
  }, [loading, me, isAuthPage, router]);

  if (isAuthPage) return <>{children}</>;

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-500">
        Loading…
      </div>
    );
  }

  if (!me) return null;

  const isDealer = me.type === "customer_user";
  const nav = isDealer
    ? DEALER_NAV
    : STAFF_NAV.filter((item) => me.permissions.includes(item.perm));

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-4 py-3">
          <Link href={isDealer ? "/" : "/admin"} className="text-lg font-bold">
            Liger
          </Link>
          <nav className="flex flex-1 flex-wrap gap-1">
            {nav.map((item) => {
              const active =
                pathname === item.href ||
                (item.href !== "/" &&
                  item.href !== "/admin" &&
                  pathname?.startsWith(item.href));
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`rounded-lg px-3 py-1.5 text-sm ${
                    active
                      ? "bg-slate-900 text-white"
                      : "text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-slate-500 sm:inline">
              {me.name} · {me.role.replaceAll("_", " ")}
            </span>
            <button
              onClick={() => {
                tokens.clear();
                router.replace("/login");
              }}
              className="text-slate-500 hover:text-slate-900"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </div>
  );
}
