import React from "react";
import { compose } from "redux";

//adHoc allows you to add dynamic HOCs to a component
// Extract 'key' prop to prevent "key prop is being spread into JSX" error in React 19
export default func => WrappedComponent => ({ key: _key, ...props }) => {
  const calledFunc = func(props);
  const composeArgs = Array.isArray(calledFunc) ? calledFunc : [calledFunc];
  const ComposedAndWrapped = compose(...composeArgs)(WrappedComponent);
  return <ComposedAndWrapped {...props} />;
};
