from pytorch_pfn_extras.training import extension
from pytorch_pfn_extras.training import trigger as trigger_module
from torch.optim.lr_scheduler import ReduceLROnPlateau


def _get_value_from_log_report(manager, key):
    # Find and return the latest reported "key" from LogReport
    if key is None:
        return None
    if key not in manager.observation:
        raise ValueError(
            '{} is not found in the reported values {}'.format(
                key, manager.observation))

    return manager.observation[key]


def _default_stepper(manager, scheduler):
    if isinstance(scheduler, ReduceLROnPlateau):
        LRScheduler.step_by_value('val/loss')(manager, scheduler)
    else:
        scheduler.step()


class LRScheduler(extension.Extension):
    """Trainer extension to adjust the learning rate using PyTorch's learning
    rate scheduler.

    This extension calls `step()` method of the given LR scheduler.
    (`torch.option.lr_scheduler.*`). When using `ReduceLROnPlateau`, the
    latest reported `val/loss` value will be used. This behavior can be
    customized by passing a custom `stepper` function.

    Args:
        scheduler (_LRScheduler or ReduceLROnPlateau): Any instance of
            `torch.optim.lr_scheduler.*`.
        stepper (callable): Function that performs the step on
            the scheduler.
        trigger: Frequency to call this extension.
    """

    def __init__(
            self, scheduler, *,
            stepper=_default_stepper,
            trigger=(1, 'epoch')):
        self.scheduler = scheduler
        self.trigger = trigger_module.get_trigger(trigger)
        self.stepper = stepper

    def __call__(self, manager):
        self.stepper(manager, self.scheduler)

    @staticmethod
    def step_by_value(key):
        def _stepper(manager, scheduler):
            scheduler.step(_get_value_from_log_report(manager, key))
        return _stepper

    def state_dict(self):
        return {'scheduler': self.scheduler.state_dict()}

    def load_state_dict(self, state):
        self.scheduler.load_state_dict(state['scheduler'])
