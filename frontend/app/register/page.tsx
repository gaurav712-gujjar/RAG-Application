"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import AuthForm from "@/components/AuthForm";
import { getToken } from "@/lib/api";

export default function Page() {
  const router = useRouter();
  // Already signed in: go straight to the chat.
  useEffect(() => {
    if (getToken()) router.replace("/");
  }, [router]);
  return <AuthForm mode="register" />;
}
