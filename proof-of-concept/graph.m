%Written by Ben Shin, 11/6/2025. Email benwshin@gmail.com for help.
fileName = 'A1.csv' %change csv name to the file you want to open.
mode = 'wavelength' %modes are GuHCl or wavelength
valueToPlot = 508; %If mode is GuHCl, specify the [GuHCl]. If mode is wavelength, specify wavelength
%[GuHCl] concs: 0, 0.4, 0.8, 1.2, 1.6, 2, 2.4, 2.8, 3.2, 3.6, 4, 4.4, 4.8,
%5.2, 5.6, 6
%wavelengths: 500-560

data = readcell(fileName);
header = string(data(1, 2:end));
GuHCl = cell2mat(data(2:end, 1));
intensity = cell2mat(data(2:end, 2:end));

wavelengths = str2double(header);

figure;hold on;

%I'll try to have it automatically fit the curves and calculate
%the free energy of folding. Maybe...

switch lower(mode)
    case 'guhcl'
        idx = find (GuHCl == valueToPlot, 1);
        if isempty(idx)
            error('Specified concentration not found')
        end
        y = intensity(idx,:);
        plot(wavelengths, y, 'o', 'LineWidth', 2);
        xlabel('Wavelength (nm)');
        ylabel('Fluorescence Intensity');
        title(sprintf('Emission Spectrum at [GuHCl] = %.2f M', valueToPlot));

    case 'wavelength'
        [~, idx] = min(abs(wavelengths - valueToPlot));
        y = intensity(:, idx);
        plot(GuHCl, y, 'o', 'LineWidth', 2);
        xlabel('[GuHCl] (M)');
        ylabel('Fluorescence Intensity');
        title(sprintf('Fluorescence vs [GuHCl] at %.1f nm', wavelengths(idx)));

    otherwise
        error('Mode isnt right')

end

grid on; box on;
