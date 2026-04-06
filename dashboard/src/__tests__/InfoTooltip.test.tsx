import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { InfoTooltip } from "../components/InfoTooltip";

describe("InfoTooltip", () => {
  it("shows explanatory text after operator interaction", async () => {
    const user = userEvent.setup();

    render(
      <InfoTooltip
        label="Что это?"
        title="Статус research"
        what="Показывает, доступны ли новости и research-слой."
        why="Это влияет на качество reasoning."
        how="Смотрите на ON, DEGRADED или OFF."
      />,
    );

    await user.click(screen.getByRole("button", { name: "Что это?" }));

    expect(screen.getByText("Статус research")).toBeInTheDocument();
    expect(screen.getByText("Что это")).toBeInTheDocument();
    expect(screen.getByText("Показывает, доступны ли новости и research-слой.")).toBeInTheDocument();
    expect(screen.getByText("Почему важно")).toBeInTheDocument();
    expect(screen.getByText("Это влияет на качество reasoning.")).toBeInTheDocument();
    expect(screen.getByText("Как читать")).toBeInTheDocument();
    expect(screen.getByText("Смотрите на ON, DEGRADED или OFF.")).toBeInTheDocument();
  });

  it("hides the tooltip after the cursor leaves the trigger area", async () => {
    const user = userEvent.setup();

    render(
      <InfoTooltip
        label="Что это?"
        title="Статус research"
        what="Показывает, доступны ли новости и research-слой."
        why="Это влияет на качество reasoning."
        how="Смотрите на ON, DEGRADED или OFF."
      />,
    );

    const button = screen.getByRole("button", { name: "Что это?" });

    await user.hover(button);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.unhover(button);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("closes the tooltip after a second click or a click outside", async () => {
    const user = userEvent.setup();

    render(
      <div>
        <button type="button">Снаружи</button>
        <InfoTooltip
          label="Что это?"
          title="Статус research"
          what="Показывает, доступны ли новости и research-слой."
          why="Это влияет на качество reasoning."
          how="Смотрите на ON, DEGRADED или OFF."
        />
      </div>,
    );

    const trigger = screen.getByRole("button", { name: "Что это?" });

    await user.click(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.click(trigger);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.click(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Снаружи" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("keeps only one tooltip open at a time", async () => {
    const user = userEvent.setup();

    render(
      <div>
        <InfoTooltip
          label="Что это? Первый"
          title="Первый tooltip"
          what="Первое описание."
          why="Первая причина."
          how="Первый способ чтения."
        />
        <InfoTooltip
          label="Что это? Второй"
          title="Второй tooltip"
          what="Второе описание."
          why="Вторая причина."
          how="Второй способ чтения."
        />
      </div>,
    );

    await user.click(screen.getByRole("button", { name: "Что это? Первый" }));
    expect(screen.getByText("Первый tooltip")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Что это? Второй" }));
    expect(screen.queryByText("Первый tooltip")).not.toBeInTheDocument();
    expect(screen.getByText("Второй tooltip")).toBeInTheDocument();
  });
});
