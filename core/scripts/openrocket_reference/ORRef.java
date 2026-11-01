import info.openrocket.core.startup.OpenRocketCore;
import info.openrocket.core.rocketcomponent.*;
import info.openrocket.core.motor.*;
import info.openrocket.core.file.motor.RASPMotorLoader;
import info.openrocket.core.document.Simulation;
import info.openrocket.core.simulation.*;
import info.openrocket.core.aerodynamics.*;
import info.openrocket.core.logging.WarningSet;
import info.openrocket.core.masscalc.*;
import info.openrocket.core.rocketcomponent.position.AxialMethod;
import info.openrocket.core.util.Coordinate;
import java.io.*;
import java.util.*;

/**
 * Builds the Mises rocketry reference rockets (tests/rocketry/reference.py) in OpenRocket's own
 * core and prints CG / CP / Cd / apogee: the numbers the rocketry tests compare against.
 *
 * Run (Java 17+, OpenRocket-24.12.jar from github.com/openrocket/openrocket/releases, not
 * shipped with Mises):
 *   javac -cp OpenRocket-24.12.jar ORRef.java
 *   java -Djava.awt.headless=true -cp OpenRocket-24.12.jar:. ORRef AeroTech_F52C.eng      # "A"
 *   java -Djava.awt.headless=true -cp OpenRocket-24.12.jar:. ORRef Estes_G40W.eng bt      # "B"
 * (the .eng files are core/src/mises/rocketry/motors/).
 */
public class ORRef {
    static ThrustCurveMotor load(String path) throws Exception {
        try (InputStream in = new FileInputStream(path)) {
            List<ThrustCurveMotor.Builder> bs = new RASPMotorLoader().load(in, path, false);
            return bs.get(0).build();
        }
    }

    public static void main(String[] a) throws Exception {
        OpenRocketCore.initialize();
        String eng = a[0];
        boolean boattail = a.length > 1 && a[1].equals("bt");
        Rocket rocket = new Rocket();
        AxialStage stage = new AxialStage();
        rocket.addChild(stage);
        NoseCone nose = new NoseCone(Transition.Shape.OGIVE, 0.200, 0.033);
        nose.setShapeParameter(1.0);   // tangent ogive
        nose.setThickness(0.002);
        nose.setFinish(ExternalComponent.Finish.NORMAL);
        nose.setMassOverridden(true); nose.setOverrideMass(0.080);
        nose.setCGOverridden(true); nose.setOverrideCGX(0.130);
        stage.addChild(nose);
        BodyTube body = new BodyTube(0.700, 0.033, 0.0015);
        body.setFinish(ExternalComponent.Finish.NORMAL);
        body.setMassOverridden(true); body.setOverrideMass(0.250);
        body.setCGOverridden(true); body.setOverrideCGX(0.350);
        body.setMotorMount(true);
        stage.addChild(body);
        TrapezoidFinSet fins = new TrapezoidFinSet(3, 0.120, 0.050, 0.060, 0.065);
        fins.setThickness(0.0032);
        fins.setCrossSection(FinSet.CrossSection.SQUARE);
        fins.setFinish(ExternalComponent.Finish.NORMAL);
        fins.setAxialMethod(AxialMethod.BOTTOM);
        fins.setAxialOffset(0.0);
        fins.setMassOverridden(true); fins.setOverrideMass(0.090);
        fins.setCGOverridden(true); fins.setOverrideCGX(0.070);
        body.addChild(fins);
        MassComponent payload = new MassComponent(0.100, 0.025, 0.120);
        payload.setAxialMethod(AxialMethod.TOP);
        payload.setAxialOffset(0.100);
        body.addChild(payload);
        if (boattail) {
            Transition bt = new Transition();
            bt.setShapeType(Transition.Shape.CONICAL);
            bt.setLength(0.034);
            bt.setForeRadiusAutomatic(false); bt.setForeRadius(0.033);
            bt.setAftRadiusAutomatic(false); bt.setAftRadius(0.020);
            bt.setThickness(0.002);
            bt.setFinish(ExternalComponent.Finish.NORMAL);
            bt.setMassOverridden(true); bt.setOverrideMass(0.020);
            bt.setCGOverridden(true); bt.setOverrideCGX(0.015);
            stage.addChild(bt);
        }
        ThrustCurveMotor motor = load(eng);
        FlightConfigurationId id = new FlightConfigurationId();
        rocket.createFlightConfiguration(id);
        rocket.setSelectedConfiguration(id);
        FlightConfiguration cfg;
        MotorConfiguration mc = new MotorConfiguration(body, id);
        mc.setMotor(motor);
        mc.setEjectionDelay(1000);
        body.setMotorConfig(mc, id);
        body.setMotorOverhang(boattail ? 0.034 : 0.0);
        rocket.enableEvents();
        cfg = rocket.getSelectedConfiguration();
        cfg.addMotor(mc);

        WarningSet w = new WarningSet();
        FlightConditions fc = new FlightConditions(cfg);
        fc.setMach(0.3);
        fc.setAOA(0);
        AerodynamicCalculator calc = new BarrowmanCalculator();
        Coordinate cp = calc.getCP(cfg, fc, w);
        RigidBody dry = MassCalculator.calculateStructure(cfg);
        RigidBody wet = MassCalculator.calculateLaunch(cfg);
        System.out.printf(Locale.ROOT, "motor %s len %.4f launch_mass %.5f prop %.5f%n", motor.getDesignation(),
                motor.getLength(), motor.getLaunchMass(), motor.getLaunchMass() - motor.getBurnoutMass());
        System.out.printf(Locale.ROOT, "cp_x %.5f cn_alpha %.4f%n", cp.x, cp.weight);
        System.out.printf(Locale.ROOT, "dry_mass %.5f dry_cg %.5f wet_mass %.5f wet_cg %.5f%n",
                dry.getMass(), dry.getCM().x, wet.getMass(), wet.getCM().x);
        System.out.printf(Locale.ROOT, "stability_cal_launch %.4f%n", (cp.x - wet.getCM().x) / 0.066);
        for (double m : new double[]{0.1, 0.2, 0.3, 0.5}) {
            fc.setMach(m);
            AerodynamicForces f = calc.getAerodynamicForces(cfg, fc, w);
            System.out.printf(Locale.ROOT, "mach %.2f cd %.4f friction %.4f pressure %.4f base %.4f%n", m,
                    f.getCD(), f.getFrictionCD(), f.getPressureCD(), f.getBaseCD());
        }

        Simulation sim = new Simulation(rocket);
        sim.setFlightConfigurationId(id);
        SimulationOptions o = sim.getOptions();
        o.setLaunchRodLength(1.0);
        o.setLaunchRodAngle(0);
        o.setWindSpeedAverage(0);
        o.setWindSpeedDeviation(0);
        o.setWindTurbulenceIntensity(0);
        o.setLaunchAltitude(0);
        o.setISAAtmosphere(true);
        o.setTimeStep(0.01);
        sim.simulate();
        FlightData d = sim.getSimulatedData();
        System.out.printf(Locale.ROOT, "apogee %.2f max_v %.2f max_acc %.2f t_apogee %.3f rod_v %.2f max_mach %.4f%n",
                d.getMaxAltitude(), d.getMaxVelocity(), d.getMaxAcceleration(), d.getTimeToApogee(),
                d.getLaunchRodVelocity(), d.getMaxMachNumber());
        System.out.println("warnings " + sim.getSimulatedWarnings());
        System.exit(0);
    }
}
