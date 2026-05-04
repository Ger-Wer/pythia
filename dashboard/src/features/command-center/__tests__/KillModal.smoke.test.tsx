import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { KillModal } from "../components/KillModal";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { CommandCenterAuth } from "../api/auth";

const KILL_AUTH: CommandCenterAuth = {
  ready: true,
  killReady: true,
  apiBaseUrl: "https://gw.example",
  token: "tok-test",
  operatorId: "op-1",
  killHmacSecret: "test-hmac-secret",
};

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function openKillModal() {
  useCommandCenterStore.getState().setKillModalOpen(true);
}

afterEach(() => {
  cleanup();
  useCommandCenterStore.getState().reset();
});

describe("KillModal — live submit flow", () => {
  it("keeps FIRE disabled until reason is valid + confirm phrase matches", async () => {
    const user = userEvent.setup();
    openKillModal();
    render(<KillModal authOverride={KILL_AUTH} />);

    const fire = screen.getByTestId("cc-kill-fire");
    expect(fire).toBeDisabled();

    // Reason too short (< 8 chars) — still disabled even after correct phrase.
    await user.type(screen.getByLabelText(/Kill reason/i), "tiny");
    await user.type(screen.getByLabelText(/Confirm phrase/i), "KILL GLOBAL");
    expect(fire).toBeDisabled();

    // Pad reason so it crosses 8-char threshold.
    await user.type(screen.getByLabelText(/Kill reason/i), " enough");
    expect(fire).toBeEnabled();
  });

  it("Ctrl+Enter on the confirm input submits and renders ack_status=COMPLETE", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        jsonResponse(202, {
          command_id: "cmd-1",
          scope: "GLOBAL",
          ack_status: "COMPLETE",
          ack_detail: "drained 0 triggers",
        }),
      );

    openKillModal();
    render(<KillModal authOverride={KILL_AUTH} fetchImpl={fetchImpl as unknown as typeof fetch} />);

    await user.type(screen.getByLabelText(/Kill reason/i), "operator drill");
    const confirm = screen.getByLabelText(/Confirm phrase/i);
    await user.type(confirm, "KILL GLOBAL");
    await user.keyboard("{Control>}{Enter}{/Control}");

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));

    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("https://gw.example/api/v1/control/kill");
    const reqInit = init as RequestInit;
    const headers = reqInit.headers as Record<string, string>;
    expect(headers["Authorization"]).toBe("Bearer tok-test");
    const body = JSON.parse(reqInit.body as string);
    expect(body).toMatchObject({
      operator_id: "op-1",
      scope: "GLOBAL",
      reason: "operator drill",
    });
    expect(typeof body.signature).toBe("string");
    expect((body.signature as string).length).toBe(64);

    const result = await screen.findByTestId("cc-kill-result");
    expect(result.textContent).toMatch(/COMPLETE/);
  });

  it("plain click without Ctrl does NOT fire (two-key requirement)", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn();
    openKillModal();
    render(<KillModal authOverride={KILL_AUTH} fetchImpl={fetchImpl as unknown as typeof fetch} />);

    await user.type(screen.getByLabelText(/Kill reason/i), "operator drill");
    await user.type(screen.getByLabelText(/Confirm phrase/i), "KILL GLOBAL");

    const fire = screen.getByTestId("cc-kill-fire");
    await user.click(fire);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("MARKET scope requires market_id and uses it in the confirm phrase", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(202, {
        command_id: "cmd-2",
        scope: "MARKET",
        ack_status: "ACCEPTED",
      }),
    );

    openKillModal();
    render(<KillModal authOverride={KILL_AUTH} fetchImpl={fetchImpl as unknown as typeof fetch} />);

    const marketRadio = screen
      .getAllByRole("radio")
      .find((el) => (el as HTMLInputElement).value === "MARKET")!;
    await user.click(marketRadio);
    await user.type(screen.getByLabelText(/Market ID/i), "poly-42");
    await user.type(screen.getByLabelText(/Kill reason/i), "drain market poly-42");
    await user.type(screen.getByLabelText(/Confirm phrase/i), "KILL poly-42");
    await user.keyboard("{Control>}{Enter}{/Control}");

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchImpl.mock.calls[0]![1] as RequestInit).body as string);
    expect(body).toMatchObject({
      scope: "MARKET",
      market_id: "poly-42",
    });
  });

  it("renders error result on 401 invalid_signature without crashing", async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn().mockResolvedValue(
      jsonResponse(401, { error: "invalid_signature" }),
    );

    openKillModal();
    render(<KillModal authOverride={KILL_AUTH} fetchImpl={fetchImpl as unknown as typeof fetch} />);

    await user.type(screen.getByLabelText(/Kill reason/i), "operator drill");
    await user.type(screen.getByLabelText(/Confirm phrase/i), "KILL GLOBAL");
    await user.keyboard("{Control>}{Enter}{/Control}");

    const result = await screen.findByTestId("cc-kill-result");
    expect(result.textContent).toMatch(/invalid_signature/);
    expect(result.textContent).toMatch(/HMAC mismatch/);
  });
});
