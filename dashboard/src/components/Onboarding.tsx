import { useState, useEffect } from "react";

interface OnboardingStep {
  target: string;
  title: string;
  content: string;
  placement?: "top" | "bottom" | "left" | "right";
}

const ONBOARDING_STEPS: OnboardingStep[] = [
  {
    target: ".hero-panel",
    title: "Добро пожаловать в Pythia!",
    content: "Это главный экран оператора. Здесь вы видите состояние системы и следующий шаг для работы.",
    placement: "bottom",
  },
  {
    target: ".status-pills",
    title: "Статус системы",
    content: "Зелёный = всё работает. Жёлтый = деградация. Красный = проблемы. Проверяйте это в первую очередь.",
    placement: "bottom",
  },
  {
    target: ".attention-card-grid",
    title: "Срочные задачи",
    content: "Красные карточки = срочно. Жёлтые = сегодня. Серые = можно позже. Начинайте с красных.",
    placement: "top",
  },
  {
    target: ".nav-link",
    title: "Навигация",
    content: "Используйте клавиши 1-5 для быстрой навигации. Нажмите Shift+? чтобы увидеть все горячие клавиши.",
    placement: "right",
  },
  {
    target: ".kpi-grid",
    title: "Метрики",
    content: "Ключевые показатели: прогнозы, позиции, задачи. Следите за числами с жёлтым фоном - они требуют внимания.",
    placement: "top",
  },
];

interface OnboardingTooltipProps {
  step: OnboardingStep;
  currentStep: number;
  totalSteps: number;
  onNext: () => void;
  onSkip: () => void;
}

function OnboardingTooltip({ step, currentStep, totalSteps, onNext, onSkip }: OnboardingTooltipProps) {
  const [position, setPosition] = useState({ top: 0, left: 0 });

  useEffect(() => {
    const target = document.querySelector(step.target);
    if (target) {
      const rect = target.getBoundingClientRect();
      const placement = step.placement || "bottom";

      let top = 0;
      let left = 0;

      switch (placement) {
        case "bottom":
          top = rect.bottom + 16;
          left = rect.left + rect.width / 2;
          break;
        case "top":
          top = rect.top - 16;
          left = rect.left + rect.width / 2;
          break;
        case "left":
          top = rect.top + rect.height / 2;
          left = rect.left - 16;
          break;
        case "right":
          top = rect.top + rect.height / 2;
          left = rect.right + 16;
          break;
      }

      setPosition({ top, left });
    }
  }, [step]);

  return (
    <div
      className="onboarding-tooltip"
      style={{
        position: "fixed",
        top: `${position.top}px`,
        left: `${position.left}px`,
        transform: "translate(-50%, 0)",
      }}
    >
      <div className="onboarding-tooltip-header">
        <strong>{step.title}</strong>
        <span className="onboarding-step-counter">
          {currentStep + 1} / {totalSteps}
        </span>
      </div>
      <p className="onboarding-tooltip-content">{step.content}</p>
      <div className="onboarding-tooltip-actions">
        <button type="button" className="ghost-btn" onClick={onSkip}>
          Пропустить
        </button>
        <button type="button" onClick={onNext}>
          {currentStep === totalSteps - 1 ? "Готово" : "Далее"}
        </button>
      </div>
    </div>
  );
}

export function Onboarding() {
  const [isActive, setIsActive] = useState(false);
  const [currentStep, setCurrentStep] = useState(0);

  useEffect(() => {
    const hasSeenOnboarding = localStorage.getItem("pythia-onboarding-completed");
    if (!hasSeenOnboarding) {
      // Delay to let the page render
      setTimeout(() => setIsActive(true), 1000);
    }
  }, []);

  function handleNext() {
    if (currentStep === ONBOARDING_STEPS.length - 1) {
      handleComplete();
    } else {
      setCurrentStep((prev) => prev + 1);
    }
  }

  function handleSkip() {
    handleComplete();
  }

  function handleComplete() {
    localStorage.setItem("pythia-onboarding-completed", "true");
    setIsActive(false);
  }

  if (!isActive) return null;

  return (
    <>
      <div className="onboarding-overlay" />
      <OnboardingTooltip
        step={ONBOARDING_STEPS[currentStep]}
        currentStep={currentStep}
        totalSteps={ONBOARDING_STEPS.length}
        onNext={handleNext}
        onSkip={handleSkip}
      />
    </>
  );
}
