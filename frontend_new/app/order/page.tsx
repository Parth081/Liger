"use client";

/** Dealer order screen. Staff order-on-behalf uses /admin/orders/new. */
import { useEffect, useState } from "react";

import OrderForm from "../components/OrderForm";
import { api } from "../lib/api";

export default function OrderPage() {
  const [customerUid, setCustomerUid] = useState<string | undefined>();
  const [state, setState] = useState<string | undefined>();

  useEffect(() => {
    void (async () => {
      try {
        // A dealer's own credit view returns their uid and state
        const list = await api.get<{
          items: { customer_uid: string; state?: string }[];
        }>("/credit/ageing?limit=1");
        if (list.items[0]) {
          setCustomerUid(list.items[0].customer_uid);
          setState(list.items[0].state);
        }
      } catch {
        /* dealers post orders under their token scope; uid is optional */
      }
    })();
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">New order</h1>
      <OrderForm customerUid={customerUid} customerState={state} />
    </div>
  );
}
