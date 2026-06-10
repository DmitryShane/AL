import { useState } from "react";
import type { ServerStatsService } from "../../types/dashboard";
import { Modal } from "../ui/Modal";
import { formatDateTime } from "./serverStatsFormatters";

export function ServicesStatusCard({
  services,
  loading,
  ready,
  refreshing,
  rebooting,
  rebootMessage,
  onRefresh,
  onReboot
}: {
  services: ServerStatsService[];
  loading: boolean;
  ready: boolean | undefined;
  refreshing: boolean;
  rebooting: boolean;
  rebootMessage: string;
  onRefresh: () => void;
  onReboot: () => Promise<boolean>;
}) {
  const [rebootModalOpen, setRebootModalOpen] = useState(false);

  async function confirmReboot() {
    const rebootRequested = await onReboot();

    if (rebootRequested) {
      setRebootModalOpen(false);
    }
  }

  return (
    <section className="panel server-stats-panel server-stats-services-panel">
      <div className="server-stats-header">
        <div>
          <h2>Services</h2>
          <p className="settings-caption">Runtime status of server processes.</p>
        </div>
        <div className="server-stats-actions">
          <button className="server-stats-refresh-button" onClick={onRefresh} disabled={refreshing || rebooting}>
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button className="server-stats-reboot-button" onClick={() => setRebootModalOpen(true)} disabled={rebooting}>
            {rebooting ? "Rebooting..." : "Reboot"}
          </button>
        </div>
      </div>

      {loading ? <p className="notice">Loading service statuses...</p> : null}
      {ready === false && services.length === 0 ? <p className="notice">Preparing service statuses...</p> : null}
      {rebootMessage ? <p className="notice">{rebootMessage}</p> : null}

      {services.length > 0 ? (
        <div className="server-stats-services">
          {services.map((service) => (
            <ServiceStatus key={service.key} service={service} />
          ))}
        </div>
      ) : null}

      {rebootModalOpen ? (
        <ServerRebootConfirmModal
          saving={rebooting}
          onCancel={() => setRebootModalOpen(false)}
          onConfirm={() => void confirmReboot()}
        />
      ) : null}
    </section>
  );
}

function ServerRebootConfirmModal({
  saving,
  onCancel,
  onConfirm
}: {
  saving: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <Modal
      onBackdropClose={onCancel}
      backdropDisabled={saving}
      panelClassName="calendar-modal--scoped-activity-delete"
      ariaLabelledBy="server-reboot-title"
      ariaDescribedBy="server-reboot-desc"
    >
      <div className="scoped-delete-modal__accent" aria-hidden="true" />
      <div className="scoped-delete-modal__body">
        <header className="scoped-delete-modal__header">
          <span className="scoped-delete-modal__badge">Server action</span>
          <h2 id="server-reboot-title">Reboot production server</h2>
          <p id="server-reboot-desc" className="scoped-delete-modal__lead">
            This will reboot the host machine and restart all system services, including the backend, bots, MongoDB, and Nginx.
            The dashboard will be unavailable while the server comes back online.
          </p>
        </header>

        <p className="scoped-delete-modal__description">
          Use this only when you need a full server restart. The request is sent immediately after confirmation.
        </p>

        <div className="modal-actions scoped-delete-modal__actions">
          <button className="server-reboot-cancel-button" type="button" onClick={onCancel} disabled={saving}>
            Cancel
          </button>
          <button className="server-reboot-confirm-button" type="button" onClick={onConfirm} disabled={saving}>
            {saving ? "Requesting..." : "Reboot server"}
          </button>
        </div>
      </div>
    </Modal>
  );
}

function ServiceStatus({ service }: { service: ServerStatsService }) {
  return (
    <div className={`server-stats-service server-stats-service-${service.status}`}>
      <div className="server-stats-service-main">
        <span className="server-stats-service-dot" aria-hidden="true" />
        <div>
          <strong>{service.label}</strong>
          <span>{service.unit}</span>
        </div>
      </div>
      <div className="server-stats-service-meta">
        <strong>{formatServiceStatus(service)}</strong>
        <span>{formatServiceDetail(service)}</span>
      </div>
    </div>
  );
}

function formatServiceStatus(service: ServerStatsService): string {
  if (service.status === "running") {
    return "Running";
  }

  if (service.status === "stopped") {
    return "Stopped";
  }

  return "Unknown";
}

function formatServiceDetail(service: ServerStatsService): string {
  const state = service.subState ? `${service.activeState} / ${service.subState}` : service.activeState;

  if (service.status === "running" && service.activeEnteredAt) {
    return `${state}, since ${formatDateTime(service.activeEnteredAt)}`;
  }

  return state;
}
