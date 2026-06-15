// Baseline arm: the bare model, no skill — just the task.
module.exports = ({ vars }) => [{ role: 'user', content: vars.task }];
