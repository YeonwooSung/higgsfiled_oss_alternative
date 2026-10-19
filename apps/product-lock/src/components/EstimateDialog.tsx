"use client";

import type { Estimate } from "@oc/contracts";
import * as Dialog from "@radix-ui/react-dialog";
import { Badge, Button } from "@oc/ui";
import { useI18n } from "../i18n";

export function EstimateDialog({ estimate, onConfirm, onClose }: {
  estimate: Estimate | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <Dialog.Root open={!!estimate} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[min(420px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 rounded-panel border border-border bg-panel p-5 shadow-lg">
          <Dialog.Title className="mb-2 text-[15px] font-semibold">{t("confirmTitle")}</Dialog.Title>
          {estimate && (
            <Dialog.Description asChild>
              <div className="space-y-2 text-[13px]">
                <p className="flex items-center gap-2">
                  {estimate.profile.label}
                  {estimate.profile.is_mock && <Badge tone="mock">{t("mock")}</Badge>}
                </p>
                {estimate.uses_gpu ? (
                  <p>{t("confirmReserve", { units: estimate.reserved_units, unit: estimate.unit.replace("_", " ") })}</p>
                ) : (
                  <p>{t("confirmNoGpu")}</p>
                )}
                {estimate.price === null && <p className="text-muted">{t("confirmPriceUnknown")}</p>}
                {!estimate.profile.available && <p className="text-danger">{estimate.profile.reason}</p>}
              </div>
            </Dialog.Description>
          )}
          <div className="mt-5 flex justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>{t("cancel")}</Button>
            <Button variant="primary" onClick={onConfirm} disabled={!estimate?.profile.available && estimate?.uses_gpu}>{t("confirmStart")}</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
