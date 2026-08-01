"use client";

/** Staff: email + password (+2FA). Dealers: phone + OTP (BR-AC-09). */
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ApiException, api, tokens } from "../lib/api";
import { Button, Card, Field, inputClass } from "../components/ui";

type TokenResponse = { access_token: string; refresh_token: string };

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"dealer" | "staff">("dealer");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // dealer
  const [phone, setPhone] = useState("");
  const [otpSent, setOtpSent] = useState(false);
  const [otp, setOtp] = useState("");
  const [debugCode, setDebugCode] = useState<string | null>(null);

  // staff
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [totp, setTotp] = useState("");

  function done(response: TokenResponse, dealer: boolean) {
    tokens.set(response.access_token, response.refresh_token);
    router.replace(dealer ? "/" : "/admin");
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof ApiException ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm">
        <h1 className="mb-1 text-center text-2xl font-bold">Liger</h1>
        <p className="mb-6 text-center text-sm text-slate-500">
          Orders, credit and collections
        </p>

        <div className="mb-4 flex rounded-lg border border-slate-200 bg-white p-1">
          {(["dealer", "staff"] as const).map((option) => (
            <button
              key={option}
              onClick={() => {
                setMode(option);
                setError(null);
              }}
              className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium ${
                mode === option
                  ? "bg-slate-900 text-white"
                  : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              {option === "dealer" ? "Dealer" : "Staff"}
            </button>
          ))}
        </div>

        <Card>
          {mode === "dealer" ? (
            !otpSent ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    const result = await api.post<{
                      sent: boolean;
                      debug_code?: string;
                    }>("/auth/otp/request", { phone });
                    setOtpSent(true);
                    setDebugCode(result.debug_code ?? null);
                  });
                }}
                className="space-y-4"
              >
                <Field label="Phone number" hint="The number registered with Liger">
                  <input
                    className={inputClass}
                    inputMode="tel"
                    placeholder="+919876543210"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    autoFocus
                  />
                </Field>
                <Button type="submit" className="w-full" disabled={busy || !phone}>
                  {busy ? "Sending…" : "Send OTP"}
                </Button>
              </form>
            ) : (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    const result = await api.post<TokenResponse>(
                      "/auth/otp/verify",
                      { phone, code: otp },
                    );
                    done(result, true);
                  });
                }}
                className="space-y-4"
              >
                <Field label="Enter the 6-digit code" hint={`Sent to ${phone}`}>
                  <input
                    className={`${inputClass} text-center text-lg tracking-[0.4em]`}
                    inputMode="numeric"
                    maxLength={6}
                    value={otp}
                    onChange={(e) => setOtp(e.target.value)}
                    autoFocus
                  />
                </Field>
                {debugCode && (
                  <p className="rounded bg-slate-100 px-3 py-2 text-xs text-slate-600">
                    Local environment code: <strong>{debugCode}</strong>
                  </p>
                )}
                <Button type="submit" className="w-full" disabled={busy || otp.length < 4}>
                  {busy ? "Verifying…" : "Verify"}
                </Button>
                <button
                  type="button"
                  onClick={() => {
                    setOtpSent(false);
                    setOtp("");
                  }}
                  className="w-full text-xs text-slate-500 hover:underline"
                >
                  Use a different number
                </button>
              </form>
            )
          ) : !challenge ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  const result = await api.post<
                    TokenResponse & { requires_2fa: boolean; challenge_token?: string }
                  >("/auth/staff/login", { email, password });
                  if (result.requires_2fa && result.challenge_token) {
                    setChallenge(result.challenge_token);
                  } else {
                    done(result, false);
                  }
                });
              }}
              className="space-y-4"
            >
              <Field label="Email">
                <input
                  className={inputClass}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoFocus
                />
              </Field>
              <Field label="Password">
                <input
                  className={inputClass}
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Signing in…" : "Sign in"}
              </Button>
            </form>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void run(async () => {
                  const result = await api.post<TokenResponse>("/auth/staff/2fa", {
                    challenge_token: challenge,
                    code: totp,
                  });
                  done(result, false);
                });
              }}
              className="space-y-4"
            >
              <Field label="Authenticator code">
                <input
                  className={`${inputClass} text-center text-lg tracking-[0.4em]`}
                  inputMode="numeric"
                  maxLength={6}
                  value={totp}
                  onChange={(e) => setTotp(e.target.value)}
                  autoFocus
                />
              </Field>
              <Button type="submit" className="w-full" disabled={busy}>
                {busy ? "Verifying…" : "Verify"}
              </Button>
            </form>
          )}

          {error && (
            <p className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
              {error}
            </p>
          )}
        </Card>
      </div>
    </div>
  );
}
